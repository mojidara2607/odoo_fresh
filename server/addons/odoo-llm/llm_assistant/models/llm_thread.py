import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class LLMThread(models.Model):
    _inherit = "llm.thread"

    assistant_id = fields.Many2one(
        "llm.assistant",
        string="Assistant",
        ondelete="restrict",
        help="The assistant used for this thread",
    )

    prompt_id = fields.Many2one(
        "llm.prompt",
        string="Prompt for workflow",
        ondelete="restrict",
        tracking=True,
        help="Prompt to use for workflow",
    )

    @api.onchange("assistant_id")
    def _onchange_assistant_id(self):
        """Update provider, model and tools when assistant changes"""
        if self.assistant_id:
            self.provider_id = self.assistant_id.provider_id
            self.model_id = self.assistant_id.model_id
            self.tool_ids = self.assistant_id.tool_ids
            self.prompt_id = self.assistant_id.prompt_id
        else:
            # Clear prompt when assistant is cleared
            self.prompt_id = False

    def set_assistant(self, assistant_id):
        """Set the assistant for this thread and update related fields

        Args:
            assistant_id (int): The ID of the assistant to set

        Returns:
            bool: True if successful, False otherwise
        """
        self.ensure_one()

        # If assistant_id is False or 0, clear the assistant and its prompt
        if not assistant_id:
            return self.write({"assistant_id": False, "prompt_id": False})

        # Get the assistant record
        assistant = self.env["llm.assistant"].browse(assistant_id)
        if not assistant.exists():
            return False

        # Update the thread with the assistant and related fields
        update_vals = {
            "assistant_id": assistant_id,
            "tool_ids": [(6, 0, assistant.tool_ids.ids)],
        }
        if assistant.provider_id.id:
            update_vals["provider_id"] = assistant.provider_id.id
        if assistant.model_id.id:
            update_vals["model_id"] = assistant.model_id.id
        if assistant.prompt_id.id:
            update_vals["prompt_id"] = assistant.prompt_id.id
        return self.write(update_vals)

    def action_open_thread(self):
        """Open the thread in the chat client interface

        Returns:
            dict: Action to open the thread in the chat client
        """
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "tag": "llm_thread.chat_client_action",
            "params": {
                "default_active_id": self.id,
            },
            "context": {
                "active_id": self.id,
            },
            "target": "current",
        }

    def get_context(self, base_context=None):
        """
        Get the context to pass to prompt rendering with thread-specific enhancements.
        This is the canonical method for creating prompt context in both production and testing.

        Args:
            base_context (dict): Additional context from caller (optional)

        Returns:
            dict: Context ready for prompt rendering
        """
        context = super().get_context(base_context or {})

        # If we have an assistant with default values, add them to the context
        if self.assistant_id:
            # Get assistant's evaluated default values using the current context
            assistant_defaults = self.assistant_id.get_evaluated_default_values(context)

            # Merge assistant defaults into context
            # Assistant defaults are added first, so thread context takes precedence
            if assistant_defaults:
                context = {**assistant_defaults, **context}

        return context

    @api.model
    def get_thread_by_id(self, thread_id):
        """Get a thread record by its ID

        Args:
            thread_id (int): ID of the thread

        Returns:
            tuple: (thread, error_response)
                  If successful, error_response will be None
                  If error, thread will be None
        """
        thread = self.browse(int(thread_id))
        if not thread.exists():
            return None, {"success": False, "error": "Thread not found"}
        return thread, None

    @api.model
    def get_thread_and_assistant(self, thread_id, assistant_id=False):
        """Get thread and assistant records by their IDs

        Args:
            thread_id (int): ID of the thread
            assistant_id (int, optional): ID of the assistant, or False to clear

        Returns:
            tuple: (thread, assistant, error_response)
                  If successful, error_response will be None
                  If error, thread and/or assistant will be None
        """
        # Get thread
        thread, error = self.get_thread_by_id(thread_id)
        if error:
            return None, None, error

        # If no assistant_id, return just the thread
        if not assistant_id:
            return thread, None, None

        # Get assistant from the assistant model
        assistant, error = self.env["llm.assistant"].get_assistant_by_id(assistant_id)
        if error:
            return thread, None, error

        return thread, assistant, None

    def _thread_to_store(self, store, fields=None, **kwargs):
        """Extend base _thread_to_store to include assistant_id and prompt_id."""
        super()._thread_to_store(store, fields=fields, **kwargs)

        # Always add assistant_id and prompt_id to thread data (either value or False)
        for thread in self:
            thread_data = {
                "id": thread.id,
                "model": "llm.thread",
                "assistant_id": {
                    "id": thread.assistant_id.id,
                    "name": thread.assistant_id.name,
                    "model": "llm.assistant",
                }
                if thread.assistant_id
                else False,
                # prompt_id is defined in this module, so handle it here
                "prompt_id": {
                    "id": thread.prompt_id.id,
                    "name": thread.prompt_id.name,
                    "model": "llm.prompt",
                }
                if thread.prompt_id
                else False,
            }
            store.add_model_values("mail.thread", thread_data)

    def _extract_message_content(self, message):
        """Extract text content from a message regardless of format"""
        content = message.get("content", "")

        if isinstance(content, list) and len(content) > 0:
            return content[0].get("text", "")
        if isinstance(content, str):
            return content
        return ""

    def get_prepend_messages(self):
        """Hook: return a list of formatted messages to prepend to the conversation."""
        self.ensure_one()

        if self.prompt_id:
            try:
                # Get messages from the prompt with enhanced context
                return self.prompt_id.get_messages(self.get_context())
            except Exception as e:
                _logger.error(
                    "Error getting messages from prompt '%s': %s",
                    self.prompt_id.name,
                    e,
                )
                # Continue without prompt messages rather than failing completely
                # Post a user-friendly warning to the thread
                self.message_post(
                    body=_(
                        "Note: The prompt '%s' could not be loaded. "
                        "Continuing without it. (Error: %s)",
                    )
                    % (self.prompt_id.name, str(e)),
                )

        return []

    # Maximum tool calls allowed per user turn to prevent infinite loops.
    # Weak/local models (e.g. qwen3-coder via Ollama) sometimes call tools
    # repeatedly instead of generating a final text response. This safeguard
    # strips tool definitions after the limit, forcing a text answer.
    _MAX_TOOL_CALLS_PER_TURN = 5

    def generate_messages(self, last_message):
        """Generate messages with actual AI intelligence."""
        self.ensure_one()

        # Get last message if not provided
        if not last_message:
            try:
                last_message = self.get_latest_llm_message()
            except UserError:
                # No DB messages found - check if prepended messages have a user message
                prepend_msgs = self.get_prepend_messages()
                user_msg = next(
                    (msg for msg in prepend_msgs if msg.get("role") == "user"),
                    None,
                )

                if user_msg:
                    # Extract content from prepended user message
                    content = user_msg.get("content", [])
                    if isinstance(content, list) and content:
                        body = content[0].get("text", "")
                    else:
                        body = str(content)

                    # Create actual user message from prepended content
                    last_message = self.message_post(
                        body=body,
                        llm_role="user",
                        author_id=self.env.user.partner_id.id,
                    )
                else:
                    # No user message in prepended messages either
                    raise

        # Tool call counter and nudge message tracking for this turn
        tool_call_count = 0
        nudge_messages = []

        # Continue generation loop
        while self._should_continue(last_message):
            if last_message.llm_role in ("user", "tool"):
                if self.model_id.model_use in ("image_generation", "generation"):
                    last_message = yield from self._generate_response(last_message)
                else:
                    # After max tool calls, strip tool definitions to force text
                    force_text = tool_call_count >= self._MAX_TOOL_CALLS_PER_TURN
                    last_message = yield from self._generate_assistant_response(
                        force_text_only=force_text,
                    )
            elif last_message.llm_role == "assistant" and last_message.has_tool_calls():
                # Execute ALL tool calls from assistant message
                tool_calls = last_message.get_tool_calls()
                for tool_call in tool_calls:
                    tool_call_count += 1
                    if tool_call_count > self._MAX_TOOL_CALLS_PER_TURN:
                        _logger.warning(
                            "Thread %s: max tool calls (%d) exceeded, "
                            "stopping tool execution",
                            self.id,
                            self._MAX_TOOL_CALLS_PER_TURN,
                        )
                        break
                    tool_message = yield from self._execute_tool_call(
                        tool_call,
                        last_message,
                    )
                    last_message = tool_message
                    self.env.cr.commit()

                # After tool execution, inject a nudge message to guide the
                # LLM toward generating a natural language answer instead of
                # calling more tools. The nudge is cleaned up after use.
                if tool_call_count <= self._MAX_TOOL_CALLS_PER_TURN:
                    nudge = self._inject_tool_result_nudge()
                    if nudge:
                        nudge_messages.append(nudge)
            else:
                _logger.info(
                    f"Breaking loop. Last message role: {last_message.llm_role}, "
                    f"has_tool_calls: {last_message.has_tool_calls()}",
                )
                break

        # Clean up nudge messages — mark as is_error so they are excluded
        # from future LLM contexts (get_llm_messages filters is_error=True)
        for nudge in nudge_messages:
            nudge.write({"is_error": True})

        return last_message

    def _inject_tool_result_nudge(self):
        """Inject a system message after tool results to guide the LLM.

        Creates a temporary system message that tells the LLM to respond
        with natural language instead of calling more tools. The message
        is cleaned up (marked is_error=True) after the LLM responds.

        Returns:
            mail.message: The nudge message record
        """
        return self.message_post(
            body=(
                "You now have the tool result above. "
                "Please give the user a clear, friendly final answer "
                "in plain English based on the data. "
                "Do NOT call any more tools. "
                "Do NOT show raw JSON or SQL."
            ),
            llm_role="system",
            author_id=False,
        )

    def _generate_response(self, last_message):
        raise NotImplementedError

    def _generate_assistant_response(self, force_text_only=False):
        """Generate assistant response and handle tool calls.

        Args:
            force_text_only: If True, strip tool definitions from the request
                so the LLM is forced to generate a text response. Used after
                max tool calls to break infinite tool-calling loops.

        Catches LLM API errors and posts them as error messages in the thread
        so users can see what went wrong without checking server logs.
        """
        # Flush any pending writes to ensure latest messages are visible
        self.env.flush_all()

        # Use the new optimized method for LLM context
        message_history = self.get_llm_messages()

        # Determine if we should use streaming
        use_streaming = getattr(self.model_id, "supports_streaming", True)

        chat_kwargs = self._prepare_chat_kwargs(
            message_history, use_streaming, force_text_only=force_text_only,
        )

        try:
            if use_streaming:
                # Handle streaming response - process tool calls directly from stream
                stream_response = self.sudo().model_id.chat(**chat_kwargs)
                assistant_message = yield from self._handle_streaming_response(
                    stream_response,
                )
            else:
                # Handle non-streaming response
                response = self.sudo().model_id.chat(**chat_kwargs)
                assistant_message = yield from self._handle_non_streaming_response(
                    response,
                )
        except Exception as e:
            # Post error message to thread so user can see it
            _logger.exception("LLM API error in thread %s", self.id)
            error_message, event = self._post_error_message(
                e,
                title=_("LLM API Error"),
            )
            yield event
            return error_message

        return assistant_message

    def _prepare_chat_kwargs(self, message_history, use_streaming, force_text_only=False):
        """Prepare chat kwargs for provider. Can be overridden by extensions.

        Args:
            message_history: mail.message recordset of conversation messages
            use_streaming: Whether to use streaming response
            force_text_only: If True, strip tool definitions so the LLM
                cannot call tools and must generate a text response
        """
        kwargs = {
            "messages": message_history,
            "tools": self.tool_ids,
            "stream": use_streaming,
            "prepend_messages": self.get_prepend_messages(),
        }

        if force_text_only:
            # Remove tool definitions — LLM cannot call tools, must generate text
            kwargs["tools"] = self.env["llm.tool"]  # empty recordset
            _logger.info(
                "Thread %s: forcing text-only response (tools stripped after "
                "reaching %d tool calls)",
                self.id,
                self._MAX_TOOL_CALLS_PER_TURN,
            )

        return kwargs

    def get_llm_messages(self, limit=25):
        """Get the most recent LLM messages in chronological order.

        This method is optimized for LLM context preparation:
        - Always returns messages in chronological order (ASC)
        - Limits to the most recent N messages for context window management
        - Uses efficient database queries with proper indexing
        - Excludes error messages (is_error=True) from context

        Args:
            limit (int): Maximum number of recent messages to retrieve (default: 25)

        Returns:
            mail.message recordset: Recent LLM messages in chronological order
        """
        self.ensure_one()

        # Domain for filtering LLM messages only (excluding error messages)
        domain = [
            ("model", "=", self._name),
            ("res_id", "=", self.id),
            ("llm_role", "!=", False),  # Only messages with LLM roles
            ("is_error", "=", False),  # Exclude error messages from LLM context
        ]

        if limit:
            # Two-step approach for efficiency:
            # 1. Get the N most recent messages (DESC order)
            recent_messages = self.env["mail.message"].search(
                domain,
                order="create_date DESC, write_date DESC, id DESC",
                limit=limit,
            )
            # 2. Sort them chronologically for LLM context (ASC order)
            return recent_messages.sorted(lambda m: (m.create_date, m.write_date, m.id))
        # If no limit, get all messages in chronological order
        return self.env["mail.message"].search(
            domain,
            order="create_date ASC, write_date ASC, id ASC",
        )

    def get_latest_llm_message(self):
        """Get the most recent LLM message for flow control.

        Returns:
            mail.message: The latest LLM message

        Raises:
            UserError: If no LLM messages exist
        """
        self.ensure_one()

        domain = [
            ("model", "=", self._name),
            ("res_id", "=", self.id),
            ("llm_role", "!=", False),
        ]

        result = self.env["mail.message"].search(
            domain,
            order="create_date DESC, write_date DESC, id DESC",
            limit=1,
        )

        if not result:
            raise UserError("No LLM messages found in this thread.")

        return result[0]

    def _should_continue(self, last_message):
        """Simplified continue logic based on message history."""
        if not last_message:
            return False

        # Continue if:
        # 1. Last message is user message → generate assistant response
        # 2. Last message is tool message → generate assistant response
        # 3. Last message is assistant with tool calls → execute tools
        if last_message.llm_role in ("user", "tool") or (
            last_message.llm_role == "assistant" and last_message.has_tool_calls()
        ):
            return True

        return False

    def _handle_streaming_response(self, stream_response):
        """Handle streaming response from LLM provider with tool call processing."""
        message = None
        accumulated_content = ""
        collected_tool_calls = []

        for chunk in stream_response:
            # Initialize message on first content
            if message is None and chunk.get("content"):
                message = self.message_post(
                    body="Thinking...",
                    llm_role="assistant",
                    author_id=False,
                )
                yield {"type": "message_create", "message": message.to_store_format()}

            # Handle content streaming
            if chunk.get("content"):
                accumulated_content += chunk["content"]
                message.write({"body": self._process_llm_body(accumulated_content)})
                yield {"type": "message_chunk", "message": message.to_store_format()}

            # Collect tool calls for processing
            if chunk.get("tool_calls"):
                collected_tool_calls.extend(chunk["tool_calls"])
                _logger.debug(
                    f"Collected {len(chunk['tool_calls'])} tool calls from chunk",
                )

            # Handle errors
            if chunk.get("error"):
                yield {"type": "error", "error": chunk["error"]}
                return message

        # Save tool calls to DB for the generation loop, but don't push
        # intermediate tool-call messages to the frontend chat UI.
        if collected_tool_calls:
            body_json = {"tool_calls": collected_tool_calls}

            if not message:
                # Tool-only response: save to DB but don't show in chat
                message = self.message_post(
                    body="",
                    body_json=body_json,
                    llm_role="assistant",
                    author_id=False,
                )
                self.env.cr.commit()
                # Don't yield message_create — this is an internal message
            else:
                # Message has text AND tool calls: save tool_calls to DB,
                # but don't push the update (text is already visible)
                message.write({"body_json": body_json})
                self.env.cr.commit()
        elif message and accumulated_content:
            # Final update for assistant message without tool calls
            message.write({"body": self._process_llm_body(accumulated_content)})
            yield {"type": "message_update", "message": message.to_store_format()}

        return message

    def _handle_non_streaming_response(self, response):
        """Handle non-streaming response from LLM provider."""
        # Extract content and tool calls from response
        content = response.get("content", "")
        tool_calls = response.get("tool_calls", [])

        if not content and not tool_calls:
            content = "No response from model"

        # Prepare body_json with tool calls if present
        body_json = {"tool_calls": tool_calls} if tool_calls else None

        # Create assistant message (always saved to DB for LLM context)
        assistant_message = self.message_post(
            body=self._process_llm_body(content) if content else "",
            body_json=body_json,
            llm_role="assistant",
            author_id=False,
        )

        # Only push to frontend if there's actual text content for the user.
        # Tool-only responses are internal — the user will see the final
        # text response after the LLM processes the tool result.
        if content:
            yield {
                "type": "message_create",
                "message": assistant_message.to_store_format(),
            }
        return assistant_message

    def _execute_tool_call(self, tool_call, assistant_message):
        """Execute a single tool call silently (hidden from chat UI).

        Tool messages are saved to the database for LLM context but are NOT
        pushed to the frontend. The user only sees the final assistant text
        response after the tool result is processed by the LLM.

        Args:
            tool_call (dict): Tool call data from assistant message
            assistant_message (mail.message): The assistant message that contains the tool calls

        Returns:
            mail.message: The tool message with execution result
        """
        try:
            # Create tool message in DB (for LLM context) but don't push to UI
            tool_msg = self.env["mail.message"].post_tool_call(
                tool_call,
                thread_model=self,
            )

            # Execute the tool call, consuming all events internally
            # (don't forward tool execution details to the frontend)
            result_msg = tool_msg
            gen = tool_msg.execute_tool_call(thread_model=self)
            try:
                while True:
                    next(gen)
            except StopIteration as e:
                if e.value is not None:
                    result_msg = e.value

            return result_msg

        except Exception as e:
            _logger.error(f"Error executing tool call: {e}")

            # Create error tool message in DB (LLM will see the error and
            # generate a user-friendly response like "I couldn't retrieve that")
            try:
                error_msg = self.env["mail.message"].create_tool_error_message(
                    tool_call,
                    str(e),
                    thread_model=self,
                )
                return error_msg
            except Exception as e2:
                _logger.error(f"Failed to create error message: {e2}")
                raise e from e2
        # Unreachable yield — required to make this a generator for yield-from
        yield  # pragma: no cover
