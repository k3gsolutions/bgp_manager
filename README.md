# BGP Manager

Aplicação web para inventário e operações BGP (Huawei VRP, SSH/SNMP), multi-empresa e RBAC.

## Requisitos

- Python 3.12+ (backend)
- Node 20+ (frontend)

## Configuração rápida (desenvolvimento)

1. **Backend** (`backend/`):

   ```bash
   cd backend
   python -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   cp .env.example .env
   ```

   Edite `.env`:

   - `FERNET_KEY` — obrigatório para cifrar senhas SSH dos equipamentos (ver comentário em `.env.example`).
   - `JWT_SECRET` — recomendado mesmo em dev; em `APP_ENV=production` é **obrigatório** (≥32 caracteres aleatórios).
   - `DATABASE_URL` — por defeito SQLite local; para PostgreSQL veja `.env.example`.

2. Arranque da API:

   ```bash
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

3. **Frontend** (`frontend/`):

   ```bash
   cd frontend
   npm install
   cp .env.example .env.local   # opcional
   npm run dev
   ```

   O Vite (porta 5174) faz proxy de `/api` para `http://127.0.0.1:8000` por defeito (`vite.config.js`).

## PostgreSQL (opcional)

Na raiz do repositório existe `docker-compose.yml` apenas com Postgres. Defina `POSTGRES_PASSWORD` no ambiente antes de usar em qualquer ambiente partilhado. Alinhe `DATABASE_URL` no `backend/.env` com o mesmo utilizador/base.

## Segurança e publicação

- Nunca commite `backend/.env`, ficheiros `*.db` de dados reais, `backend/logs/`, `frontend/dist/` ou `node_modules/`.
- Leia [SECURITY.md](./SECURITY.md) e use `APP_ENV=production` só com segredos fortes e CORS restrito.
- A API aplica JWT + permissões por rota; não confie apenas na UI.

## Documentação adicional

- `docs/context.md` — contexto funcional
- `CHANGELOG.md` — alterações
