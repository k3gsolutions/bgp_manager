# BGP Manager — Resumo da Ferramenta e Roadmap

## Resumo do que já foi entregue

### Base da plataforma

- Backend FastAPI + frontend React/Vite funcionando.
- CRUD de dispositivos com credenciais criptografadas.
- Teste real de conectividade SSH por dispositivo.
- Coleta SSH Huawei para inventário.
- Auto-validação de acessibilidade no startup (SSH + SNMP por dispositivo).

### SNMP e inventário

- Coleta completa SNMP (`/snmp/collect`) com persistência de interfaces, peers BGP e VRFs.
- Refresh leve de status (`/snmp/status-refresh`) sem gerar novo inventário.
- Histórico de mudanças de inventário.
- Atualização automática no frontend (coleta completa a cada 5 min).
- Coleta não remove mais dados: elementos ausentes ficam inativos (soft-disable) e permanecem consultáveis no banco.
- Fallback SSH para peers BGP IPv6 quando SNMP não expõe sessões IPv6.
- Normalização de netmask SNMP para manter IPv4 em CIDR no frontend.
- Limpeza automática de interfaces legadas com sufixo de capacidade (ex.: `(40G)`) quando duplicadas por nome-base.

### BGP (operação)

- Painel BGP com:
  - filtro por estado,
  - filtro por papel (Cliente/Operadora/IX/CDN),
  - checkbox para incluir iBGP (padrão eBGP),
  - busca por IP/ASN.
- Alteração de papel do peer em modo draft + botão `Salvar`.
- Persistência de filtros por dispositivo durante a sessão.
- Peers inativos aparecem tachados e podem ser desativados manualmente sem delete físico.
- `LOCAL IP` com fallback por interface relacionada quando ausente na sessão.
- Contadores de rota por peer obtidos de `display bgp peer verbose`:
  - `ADV ROUTE` (rotas recebidas),
  - `REC ROUTE` (rotas anunciadas).

### Investigação de prefixo (SSH)

- Consulta por IP/CIDR/ASN.
- Extração de atributos BGP:
  - AS-Path, Origin, LocalPref, MED, NextHop.
- Detecção de prepend.
- Extração de communities (standard, extended, large).
- Validação de export para peers operadora via `advertised-routes`.
- Exibição em painel dedicado (`Busca de Prefixo`).

### Dashboard

- Aba Dashboard funcional com:
  - lista de dispositivos;
  - status SSH e SNMP por dispositivo;
  - ações rápidas de teste SSH e coleta SNMP.

### Logs

- Logs persistidos com rotação e compressão.
- Endpoint de leitura de eventos recentes para a UI.
- Aba de log exibindo os últimos 100 eventos (inclusive falhas).
- Filtros na aba de logs por nível e origem.

### Interfaces

- Interfaces inativas aparecem tachadas e possuem ação de remoção lógica (desativar no banco).

## Estado operacional atual

- Foco principal: Huawei VRP.
- Interface de operação diária já utilizável.
- Banco de dados suporta `DATABASE_URL` (PostgreSQL/SQLite); ambiente local atual funciona com ajustes para polling frequente.
- Ajustes recentes de estabilidade:
  - isolamento de refresh SNMP leve x coleta completa;
  - proteção contra esgotamento de pool em SQLite (`NullPool`);
  - tuning de SSH no lookup BGP (`fast_cli=False`, timeouts maiores).

## Diretriz de fonte de código

- `./00-Material/*` deve ser usado apenas como consulta e inspiração técnica.
- O desenvolvimento oficial deve ser consolidado exclusivamente nos módulos do produto (`backend/` e `frontend/`).
- Antes de incorporar trechos de referência, aplicar padronização de arquitetura, logs, validações e contratos de API da aplicação.

## Diretriz de documentação

- Toda alteração funcional deve ser registrada no `CHANGELOG.md`.
- Além do changelog, atualizar também `docs/context.md` e este `docs/roadmap.md`.

## Mapa rápido de módulos (alto nível)

- Backend API: `backend/app/main.py`
- Router de dispositivos/SSH: `backend/app/routers/devices.py`
- Router de SNMP/BGP/Inventário: `backend/app/routers/snmp.py`
- Serviço de lookup BGP: `backend/app/services/bgp_export_lookup.py`
- Serviço SNMP full: `backend/app/services/snmp_inventory.py`
- Serviço SNMP leve: `backend/app/services/snmp_status_refresh.py`
- Persistência e diff: `backend/app/services/inventory_persist.py`
- Frontend orquestração/polling: `frontend/src/App.jsx`
- Árvore e navegação de dispositivos: `frontend/src/components/DeviceTree.jsx`
- Painel BGP: `frontend/src/pages/BGPPanel.jsx`
- Painel Interfaces: `frontend/src/pages/InterfacesPanel.jsx`
- Painel busca de prefixo: `frontend/src/pages/BgpLookupPanel.jsx`

## Próximos incrementos recomendados

### Curto prazo

- [x] Adicionar filtros por origem e nível na aba de logs.
- [ ] Incluir busca textual e exportação na aba de logs.
- [ ] Adicionar debounce/cancelamento de consultas no painel de busca de prefixo.
- [ ] Criar testes automáticos para parsers de saída VRP.
- [ ] Adicionar ação de reativação manual para itens inativos (interface/peer), além da reativação automática por coleta.

### Médio prazo

- [ ] Endpoint e UI para diff de configurações coletadas.
- [ ] Métricas históricas de interface com gráficos.
- [ ] Estratégia de retries/backoff para polling SNMP.
- [ ] Exportação de relatórios (CSV/JSON) de peers e investigação.

### Evolução de produto

- [ ] Suporte estruturado para outros vendors (Cisco/Juniper).
- [x] Autenticação de usuários e RBAC (JWT, empresas, escopo por `company_id`, perfis e permissões).
- [ ] Overrides finos por dispositivo (exceções de escopo além da empresa).
- [ ] Auditoria detalhada de ações operacionais.
- [ ] Pipeline de CI com testes e lint automatizados.

## Execução local

```bash
# backend
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# Defina JWT_SECRET e BOOTSTRAP_SUPERADMIN_PASSWORD em produção (ver CHANGELOG).
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# frontend
cd frontend
npm install
npm run dev -- --host 0.0.0.0
```

- Frontend: `http://localhost:5174` (ou porta livre equivalente do Vite)
- API docs: `http://localhost:8000/docs`
