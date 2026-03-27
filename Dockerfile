FROM odoo:19.0

USER root

# Install PostgreSQL client and Python dependencies for LLM modules
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*
COPY ./server/addons/odoo-llm/requirements.txt /tmp/llm-requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages --ignore-installed typing-extensions packaging \
    && pip3 install --no-cache-dir --break-system-packages -r /tmp/llm-requirements.txt \
    && rm /tmp/llm-requirements.txt

# Copy custom addons
COPY ./server/addons /mnt/extra-addons

# Copy custom Odoo config
COPY ./odoo.conf /etc/odoo/odoo.conf

# Copy entrypoint script
COPY ./entrypoint.sh /entrypoint-init.sh
RUN chmod +x /entrypoint-init.sh

RUN chown -R odoo:odoo /mnt/extra-addons /etc/odoo/odoo.conf

USER odoo

ENTRYPOINT ["/entrypoint-init.sh"]
