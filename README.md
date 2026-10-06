# Odoo 19 Community — Base de implementación

Plantilla base para implementaciones de Odoo 19 Community. Los módulos propios
se encuentran en `custom-addons/`.

## Requisitos

- Docker y Docker Compose
- `.env` configurado a partir de `.env.example`
- Puertos locales disponibles: `127.0.0.1:19070` (Odoo) y
  `127.0.0.1:15433` (PostgreSQL)

## Desarrollo local

```bash
cp .env.example .env
docker compose up -d --build
docker compose ps
```

Acceso web: <http://localhost:19070>

PostgreSQL para administración local:

```text
Host: localhost
Puerto: 15433
Usuario: odoo_user
```

Odoo se conecta internamente a PostgreSQL mediante `db:5432`.

## Operación

```bash
docker compose logs -f odoo
docker compose restart odoo
docker compose down
```

No ejecutar `docker compose down -v` con datos importantes: elimina los
volúmenes persistentes.

## Producción

En producción, Nginx debe instalarse fuera de Docker, terminar HTTPS y hacer
proxy hacia Odoo. PostgreSQL no debe publicar puertos. Antes de actualizar,
respaldar la base de datos y el filestore de Odoo (`/var/lib/odoo`).

Los certificados, credenciales SUNAT, tokens y el archivo `.env` nunca deben
versionarse.

CODIGO PARA CUSTOMIZAR ODOO

- NOMBRE DE RECIBO DE ENTREGA PARA LAS GUIAS

(object.sunatfm_gre_number and '%s - %s' % (object.partner_id.name or '', object.sunatfm_gre_number)) or object.name
