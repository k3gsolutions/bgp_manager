# Changelog

Todas as mudanças relevantes da aplicação devem ser registradas neste arquivo.

Formato sugerido:
- data (`YYYY-MM-DD`)
- módulo/área afetada
- resumo do que mudou
- impacto operacional (quando aplicável)

## 2026-04-15

### Backend — CORS
- Ordem dos middlewares: ``UserAuditMiddleware`` primeiro, ``CORSMiddleware`` por último (FastAPI insere no índice 0 — o CORS fica mais externo e trata preflight/cabeçalhos antes do audit).
- ``allow_origin_regex`` (localhost / RFC1918 + qualquer porta) activo em **todos** os ambientes excepto ``APP_ENV=production``, alinhando ``test``/CI ao comportamento de desenvolvimento. Evita preflight falhar quando o Vite abre por IP da LAN e ``VITE_API_URL=http://127.0.0.1:8000`` (o navegador mostrava «sem resposta»).
- Testes ``backend/tests/test_cors_preflight.py`` (preflight OPTIONS + GET /health com Origin LAN).

### Frontend — API client
- ``VITE_API_URL`` com sufixo ``/api`` é normalizado para não gerar ``…/api/api``.
- Mensagem «sem resposta» com API absoluta inclui o ``Origin`` actual da página e lembra ``CORS_EXTRA_ORIGINS`` em produção ou proxy sem ``VITE_API_URL``.

### Ferramentas / qualidade
- ``npm run check`` / ``scripts/check-local.sh``: compileall, ``check_functionality.py``, ``pytest tests`` no backend, e ``vite build`` do frontend.
- Seed inicial do superadmin: INSERT SQL passa a preencher ``access_all_companies`` (evita NOT NULL em BD novo).

## 2026-04-14

### Backend — route-policy / circuito
- Novo módulo `app/services/route_policy_circuit.py`: documenta modelos de nome (`C02-TIM-EXPORT`, `C03-IMPORT-IPV4`, etc.), `extract_circuit_id`, `parse_route_policy_circuit` (operadora opcional + função) e `circuit_id_from_peer_policies` (import/export coerentes).
- Tabela `bgp_peers`: colunas `route_policy_import` / `route_policy_export`; preenchidas na coleta a partir do parser de `display bgp … peer verbose` (SSH); `GET …/bgp-peers` expõe `peer_display_name` e as policies.

### Backend — snapshots running-config
- Tabela `configurations`: colunas `source`, `content_sha256`, `byte_size`; retenção configurável (`config_snapshot_retention` / `CONFIG_SNAPSHOT_RETENTION`, padrão 30).
- `display current-configuration` **só** quando passou a janela `config_snapshot_refresh_hours` (padrão 1h, `CONFIG_SNAPSHOT_REFRESH_HOURS`) desde o último snapshot; nessa consulta corre antes dos outros `display` na mesma sessão. Dentro da janela: mesmo hash não duplica; após a janela grava nova linha mesmo com config idêntica. Serviço `app/services/config_snapshot.py` (`running_config_fetch_needed`).

### Backend — CORS (desenvolvimento)
- Com `APP_ENV=development`, uso de `allow_origin_regex` no FastAPI para aceitar origens do Vite em **qualquer porta** em `localhost` / `127.0.0.1` / `::1` e em **redes privadas** comuns (LAN), evitando falhas de preflight quando `VITE_API_URL` aponta para `http://127.0.0.1:8000`.

### Frontend — Interfaces / BGP (carga)
- Abertura das abas **Interfaces** e **BGP**: primeiro pedido é só **GET do banco** (dados já persistidos); removido o `snmp/collect` automático global no `App.jsx` (antes a cada ~5 min ao ver o equipamento).
- **SNMP em segundo plano** só dentro de cada painel (com permissão): `status-refresh` a cada **3 min** (aba visível) e **coleta completa** em fundo a cada **18 min** (primeira após **~90 s**), sem bloquear a lista.
- Após `status-refresh` + releitura do BD: **merge por `id`** quando o conjunto de linhas é o mesmo (só atualiza campos); se houver **adição/remoção** de interfaces ou peers, substitui a lista vinda do BD.

### Frontend — carregamento / diagnóstico
- `GET /auth/me` e `POST /auth/login` com **timeout** (evita spinner infinito se o backend não responder).
- `useLog()` com **fallback seguro** fora do `LogProvider` (evita crash por contexto nulo).
- **`RootErrorBoundary`**: em erro de render, mostra mensagem em vez de tela em branco.

### Frontend — BGP Peers
- Coluna **NOME**: `peer_display_name` da API — para Operadora/IX/CDN, prefixo **`Cxx-`** ao nome já resolvido por interface quando o ID de circuito é inferido das route-policies (coleta SSH verbose).
- Modal de informações do peer: **route-policy import/export** (mesma origem SSH) e nota sobre limites do SNMP vs. backup offline de `running-config`.
- Filtros de **Estado** e **Papel** passam a aceitar **múltipla seleção** (alternar botões); **Todos** limpa o filtro daquele grupo (mostra todos).
- Persistência dos filtros por equipamento na sessão (`filterStates` / `filterRoles`; migração automática dos valores antigos `filterState` / `filterRole`).
- Comparação de **estado BGP** normalizada em minúsculas (compatível com respostas mistas).

## 2026-04-13

### Segurança multiempresa (RBAC)
- Autenticação JWT: `POST /api/auth/login`, `GET /api/auth/me` (Bearer).
- Modelos `companies`, `users`, associação `user_company`; campo obrigatório `devices.company_id`.
- Perfis `superadmin`, `admin`, `operator`, `viewer` com mapa de permissões no backend (`permissions.py`).
- CRUD `/api/users` e `/api/companies` (escopo: superadmin global; admin restrito às empresas vinculadas).
- Todas as rotas de dispositivos, SNMP, logs e BGP exigem token e respeitam escopo de empresa (exceto superadmin).
- Bootstrap no startup: cria tabelas/colunas se faltarem, empresa padrão, e usuário `superadmin` quando não existir nenhum usuário (senha via `BOOTSTRAP_SUPERADMIN_PASSWORD` no `.env`, ou senha de desenvolvimento `ChangeMe!SuperAdmin` quando `APP_ENV=development`).
- Variáveis novas: `JWT_SECRET` (obrigatório em produção; em `development` há default inseguro se vazio), `JWT_EXPIRE_MINUTES`, `BOOTSTRAP_SUPERADMIN_USERNAME`, `BOOTSTRAP_SUPERADMIN_PASSWORD`.
- Dependências Python: `python-jose[cryptography]`, `passlib[bcrypt]`, `bcrypt`.

### Frontend
- Tela de login, `AuthContext`, token em `localStorage`, axios com `Authorization`.
- Páginas `CompaniesPage` e `UsersPage`; itens de menu condicionados à permissão.
- Lista e modal de equipamentos com empresa (`company_id` / `company_name`).

### Impacto operacional
- **Após atualizar**, defina `JWT_SECRET` e `BOOTSTRAP_SUPERADMIN_PASSWORD` no `.env` do backend antes de expor em produção.
- API sem token válido retorna `401` nas rotas protegidas.

## 2026-04-11

### Backend
- `bgp_peers`: ampliado o papel de peering para `Cliente`, `Operadora`, `IX`, `CDN` (`is_customer`, `is_provider`, `is_ix`, `is_cdn`).
- Atualizado contrato de API para validar exatamente um papel ativo por peer.
- `bgp-export-lookup`:
  - retry/fallback de conexão SSH para reduzir falhas intermitentes;
  - cruzamento de `Advertised to such XX peers` com peers classificados como `Operadora/IX`;
  - enriquecimento de saída com nome do peer, role, AS-Path anunciado e contagem de prepend.
- `interfaces`:
  - suporte persistente de IPv6 por interface (`ipv6_addresses`);
  - retorno de IPv4 em notação CIDR (`ipv4_cidr`);
  - retorno de `related_peers` (peers na mesma sub-rede IPv4 da interface).
- Ajustes de estabilidade do banco para cenário SQLite com polling frequente (`NullPool`).
- Logging operacional:
  - logs em arquivo com rotação por tamanho e compressão (`.gz`) ao rotacionar;
  - arquivo de eventos estruturados (JSON) para consumo da UI.
- Novo endpoint `GET /api/logs/recent?limit=100` para retornar os últimos eventos.
- Auto-validação no startup:
  - ao subir a aplicação, cada dispositivo é testado em SSH e SNMP;
  - resultados registrados no log (`STARTUP`).

### Frontend
- `BGPPanel`:
  - filtros e edição de papel com 4 opções (`Cliente`, `Operadora`, `IX`, `CDN`);
  - persistência de filtros por dispositivo.
- `BgpLookupPanel`:
  - exibição de peers anunciados com nome/role;
  - exibição de AS-Path/prepend no contexto de advertised-routes.
- `InterfacesPanel`:
  - removidas colunas de `IN/OUT octets`;
  - coluna IPv4 em CIDR;
  - nova coluna para IPv6.
- `DashboardPage` (novo):
  - lista de dispositivos cadastrados;
  - status de SSH e SNMP por dispositivo;
  - ações de `Testar SSH` e `Coletar SNMP`.
- `LogPanel`:
  - passa a consumir logs do backend;
  - exibe os últimos 100 eventos (incluindo falhas de módulos).

### Operação
- Polling automático no frontend ajustado para coleta SNMP completa a cada 5 minutos com persistência em banco.
- Diretriz formalizada: `./00-Material/*` é fonte de referência e reaproveitamento, não código oficial de produção.

### Inventário (novas regras de persistência)
- Coleta SNMP não remove mais interfaces/peers ausentes do banco.
- Itens não vistos na coleta passam para estado inativo (`is_active=false`, `deactivated_at`) mantendo histórico completo.
- Reativação automática quando interface/peer volta a aparecer em coletas SNMP.
- Novo fluxo manual de desativação lógica pela UI/API:
  - `PATCH /api/devices/{id}/interfaces/{interface_id}/deactivate`
  - `PATCH /api/devices/{id}/bgp-peers/{peer_id}/deactivate`
- UI (`BGP` e `Interfaces`) passa a exibir itens inativos com texto tachado e badge de status, sem exclusão física.

### IPv6 e observabilidade
- Coletor SNMP IPv6 ajustado para aceitar índices `ipv6z` (IP-MIB), ampliando compatibilidade de coleta.
- Coleta SNMP agora registra a origem do IPv6:
  - `snmp` quando coletado diretamente por SNMP;
  - `ssh_fallback` quando SNMP não retorna IPv6 e o equipamento Huawei é consultado via SSH (`display ipv6 interface brief`);
  - `none` quando não há IPv6 retornado.
- `InterfacesPanel` exibe contagem de IPv6 e origem da coleta no retorno da operação.
- `LogPanel` recebeu filtros por nível (`error/warn/info/success`) e por origem (`SNMP`, `SSH`, `STARTUP`, etc.).

### Correções de regressão SNMP/SSH
- Corrigida normalização de máscara IPv4 vinda por SNMP (`OctetString` binário), restaurando exibição `IP/Mask` em CIDR (ex.: `/30`).
- Coleta SNMP passou a usar `lexicographicMode=False` para evitar walk fora da subtree e reduzir travamentos/intermitência.
- Limpeza automática de interfaces legadas no padrão `nome(40G)` quando o `nome` base já existir no inventário atual, removendo lixo histórico do banco.
- Quando SNMP não retorna peers BGP IPv6, a coleta aplica fallback SSH (`display bgp ipv6 peer verbose`) e mescla os peers ao inventário para evitar marcação indevida como inativos.

### Ajustes na aba BGP
- `LOCAL IP`: quando não vem na sessão BGP, a API passa a inferir pelo IP da interface local relacionada à sub-rede do peering.
- A coleta por SSH verbose de peers BGP (IPv4/IPv6) enriquece os campos de rotas por peer:
  - `in_updates` = `Received total routes`;
  - `out_updates` = `Advertised total routes`.
- UI da aba BGP atualizada:
  - coluna `IN UPDATES` renomeada para `ADV ROUTE`;
  - coluna `OUT UPDATES` renomeada para `REC ROUTE`.

### UX da aba BGP (janela de informação)
- Adicionado botão de informação (ícone) ao lado do IP do peer.
- Clique no ícone abre janela com:
  - título em negrito no formato `ASxxxxx-NomeDoPeer`;
  - `Local IP`;
  - `Advertised-routes`;
  - `Received-routes`.
- Para liberar espaço, removidas as colunas `ADV ROUTE`, `REC ROUTE` e `LOCAL IP` da grade.
- Nova coluna `NOME` exibindo nome derivado da descrição da interface relacionada ao peering.

### Investigação de prefixo (correção de precisão)
- Consulta por CIDR explícito (ex.: `45.169.160.0/23`) passou a ser estrita, sem fallback automático para busca ampla que pode retornar subprefixos (`/24`).
- Expansão/fallback amplo permanece apenas para consultas não explícitas (sem máscara).
- Lookup passou a considerar peers classificados de todas as classes (`Operadora`, `IX`, `Cliente`, `CDN`) ao montar lista de exportação.
- Painel de investigação ganhou bloco minimalista de exportação classificada com filtro por papel.

## 2026-04-14

### Frontend (BGP / Equipamentos)
- AS-Path passou a ser renderizado em badges por ASN (visual semelhante a communities) em:
  - `BgpLookupPanel`;
  - `BGPPanel` (modal de rotas anunciadas/recebidas);
  - `BgpExportLookupModal`.
- Ajustada marcação do ASN local no AS-Path para usar `local_asn` correto do dispositivo/consulta.
- Aba `Equipamentos` simplificada para operação SSH:
  - removidos filtros de tipo (`SSH`, `WEB`, `WinBox`, `RDP`) e lógica associada;
  - mantida tipagem visual fixa como SSH.
- Opção de "limpar peers inativos antes da coleta VRP" ficou visível apenas para utilizador `superadmin`.

### Importação em lote de dispositivos
- Ativado fluxo de importação em batch na aba `Equipamentos` com modal dedicado.
- Suporte a ficheiros `CSV` e `XML` com parser no frontend (`deviceImportParser`), validação prévia e preview.
- Inclusos modelos padrão de importação:
  - `frontend/public/templates/dispositivos-exemplo.csv`
  - `frontend/public/templates/dispositivos-exemplo.xml`
- Nova API frontend: `devicesApi.batchCreate(...)`.

### Backend (segurança e robustez)
- Novo endpoint `POST /api/devices/batch` para criação em lote com:
  - validação por item (`DeviceCreate`);
  - retorno estruturado de sucessos/falhas por linha;
  - isolamento por item (falha de uma linha não desfaz as demais).
- Endurecimento de autorização:
  - `POST /api/devices/{id}/maintenance/purge-inactive-bgp-peers` restrito a `superadmin`.
  - parâmetro `purge_inactive_bgp_first` na coleta SSH é ignorado para não-superadmin (com log explícito).
- `GET /api/logs/recent` recebeu validação explícita do `limit` (`1..1000`) via `Query`.

### Segurança/configuração para publicação
- `backend/app/config.py`:
  - validações estritas para `APP_ENV=production` (exige `JWT_SECRET` forte e `FERNET_KEY`);
  - default de `DATABASE_URL` para SQLite local (sem credenciais embutidas).
- `backend/.env.example` revisado com instruções seguras e sem credenciais reais.
- `docker-compose.yml` atualizado para usar variáveis de ambiente em `POSTGRES_*` (com aviso de uso apenas dev).
- Novo `SECURITY.md` com política de reporte e recomendações de hardening.
- `README.md` reescrito para refletir corretamente o projeto e o setup atual.

### Organização de repositório / GitHub
- Base do projeto sincronizada para a raiz `60-BGP_Manager` (`backend/`, `frontend/`, `docs/`).
- Removidas referências operacionais a `.claude/worktrees/trusting-napier` nos scripts da raiz (`package.json`).
- `.gitignore` ampliado para excluir artefatos sensíveis e locais (`.claude/`, `.claire/`, `00-Material/`, logs, envs, dist, venv, dbs).
- Repositório Git local inicializado na raiz `60-BGP_Manager` e commit inicial criado para publicação limpa.

### Gerenciamento (backup/restauração)
- Nova aba `Gerenciamento` no frontend para:
  - exportar backup completo do banco em JSON;
  - importar backup JSON para restaurar ambiente em outro servidor.
- Novos endpoints backend:
  - `GET /api/management/backup/export`
  - `POST /api/management/backup/import`
- Export/import cobre tabelas do banco via metadados SQLAlchemy (incluindo usuários, empresas, dispositivos e inventário relacionado).
- Operação restrita a `superadmin` (nova permissão `management.backup` + verificação explícita no backend).

### Gerenciamento (atualização do sistema)
- Novo mecanismo completo de update na aba `Gerenciamento`:
  - exibe versão atual instalada, última versão disponível (GitHub/origin) e status;
  - botão `Verificar atualização`;
  - botão `Atualizar versão` quando há update disponível;
  - log de execução por etapas e polling em tempo real.
- Backend ganhou serviço dedicado `system_update_service` com:
  - descoberta de versão atual via git local;
  - consulta de versão remota (tags semânticas e fallback para commit de `origin/main`);
  - comparação de versões e estado persistente em memória;
  - lock para impedir atualizações concorrentes;
  - execução de update por etapas (pull, deps backend/frontend, build, migração e restart opcional);
  - rollback básico para commit anterior em falhas.
- Novos endpoints:
  - `GET /api/management/system-update/status`
  - `POST /api/management/system-update/check`
  - `POST /api/management/system-update/run`
- Configuração opcional adicionada:
  - `UPDATE_BACKEND_RESTART_CMD`
  - `UPDATE_FRONTEND_RESTART_CMD`

### BGP (received-routes de Cliente)
- Ajustada sanitização da coleta SSH de rotas recebidas (`customer-received-routes`):
  - normaliza `prefix` em CIDR válido;
  - normaliza espaços do `as_path`;
  - garante retorno apenas com os campos `prefix` (Network) e `as_path` (Path/Ogn) para a UI.
- Paginação de prefixos no modal BGP passou a funcionar em memória:
  - primeira consulta coleta até 200 prefixos (`fetch_all=true`);
  - navegação entre páginas não dispara nova consulta SSH ao equipamento.
