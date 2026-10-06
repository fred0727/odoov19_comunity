#!/bin/sh
set -eu

if [ "$(id -u)" = 0 ]; then
    chown -R odoo:odoo /var/lib/odoo
    exec runuser -u odoo -- "$0" "$@"
fi

runtime_config=/tmp/odoo.conf
umask 077
cp /etc/odoo/odoo.conf "$runtime_config"
printf '\nadmin_passwd = %s\n' "$ODOO_ADMIN_PASSWORD" >> "$runtime_config"

exec /opt/odoo-src/odoo-bin \
    --config="$runtime_config" \
    --db_host=db \
    --db_port=5432 \
    --db_user="$POSTGRES_USER" \
    --db_password="$POSTGRES_PASSWORD"
