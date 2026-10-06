FROM odoo:19.0

USER root
RUN python3 -m pip install --no-cache-dir --break-system-packages \
    "signxml==3.2.2"
COPY --chown=odoo:odoo odoo /opt/odoo-src/odoo
COPY --chown=odoo:odoo addons /opt/odoo-src/addons
COPY --chown=odoo:odoo odoo-bin /opt/odoo-src/odoo-bin
COPY --chown=odoo:odoo docker/start-odoo.sh /usr/local/bin/start-odoo
RUN chmod +x /opt/odoo-src/odoo-bin /usr/local/bin/start-odoo
