# BGP Manager — Contexto Atual do Projeto

## Objetivo da ferramenta

Aplicação web para operação de rede com foco em Huawei VRP (NE8000), centralizando:

- cadastro e teste de acesso a dispositivos;
- coleta SNMP (inventário completo e refresh de status);
- visualização de interfaces e peers BGP;
- classificação de peers (Cliente/Operadora/IX/CDN);
- investigação de prefixo/ASN via SSH (export, prepend e communities).

## Status funcional (implementado)

### Autenticação e multiempresa (RBAC)

- Login JWT (`POST /api/auth/login`); perfil e permissões em `GET /api/auth/me`.
- Empresas (`companies`), usuários (`users`), vínculo usuário↔empresa (`user_company`); cada dispositivo tem `company_id`.
- Perfis: `superadmin` (acesso total), `admin`, `operator`, `viewer` — permissões avaliadas no servidor (`app/permissions.py`).
- Rotas `/api/users/*`, `/api/companies/*`; demais APIs exigem header `Authorization: Bearer <token>`.
- Seed automático na primeira subida: empresa padrão + `superadmin` se o banco não tiver usuários (ver `CHANGELOG.md` e `.env`).

### Fluxo principal na interface

- Lista de equipamentos e árvore por cliente.
- Clique no nome do dispositivo abre a view `Busca de Prefixo` (painel inline).
- Clique em `BGP` abre tabela de peers.
- Clique em `Interfaces` abre tabela de interfaces.
- Clique em `Communities` (com permissão `communities.view`) abre o painel **por dispositivo**, no mesmo nível que Interfaces e BGP:
  - **Biblioteca**: só `ip community-filter` (basic/advanced), campo `filter_name`. Sincronização: `POST …/communities/resync-from-config` (último backup) ou `POST …/communities/resync-live` (SSH). O resync **inativa** linhas na biblioteca que foram geradas por engano a partir de nomes de `ip community-list` (filtro com nome da lista + valores da lista, quando esse nome não é também um community-filter).
  - **Community Sets**: `ip community-list`; cada membro tem `community_value` e liga à biblioteca só se existir `community-filter` com o **mesmo valor** (`missing_in_library` + alerta na UI se faltar). Apply bloqueado com ausentes até confirmação explícita extra (`acknowledge_missing_library_refs`). Sets importados são só leitura; clone gera rascunho `app_created`.

### Coleta e atualização

- Coleta SNMP completa (`/snmp/collect`) com inventário (interfaces, BGP, VRFs) e histórico.
- Refresh SNMP leve (`/snmp/status-refresh`) para atualizar apenas status/counters sem criar inventário novo.
- Coleta SNMP não apaga dados existentes: itens ausentes são apenas marcados como inativos (soft-disable).
- IPv6: coleta preferencial via SNMP; quando SNMP não retorna IPv6 em Huawei, há fallback por SSH (`display ipv6 interface brief`).
- BGP IPv6: quando SNMP (BGP4-MIB) não retorna peers IPv6, a coleta aplica fallback SSH (`display bgp ipv6 peer verbose`) para manter sessões IPv6 ativas no inventário.
- Máscara IPv4 SNMP é normalizada para dotted-decimal antes da persistência, preservando a notação CIDR no frontend.
- Coleta BGP via SSH verbose também é usada para enriquecer contadores de rotas por peer (`Received total routes` e `Advertised total routes`) durante o ciclo SNMP.
- Agendamento no frontend:
  - coleta SNMP completa a cada 5 minutos;
  - 1 coleta inicial logo após abrir painel de dispositivo.

### BGP e investigação

- BGP panel com filtros por:
  - busca (IP/ASN),
  - estado (Established/Active/Idle),
  - papel (Todos/Clientes/Operadoras/IX/CDN),
  - checkbox para incluir iBGP (padrão: somente eBGP).
- Persistência de filtros por `device.id` durante a sessão.
- Alteração de papel do peer em modo draft, com botão explícito `Salvar`.
- `LOCAL IP` usa fallback por interface relacionada quando o valor não vem na sessão BGP.
- Colunas de rota no painel BGP:
  - `ADV ROUTE` (valor de `Received total routes`);
  - `REC ROUTE` (valor de `Advertised total routes`).
- Prefix Investigation via SSH (`/ssh/bgp-export-lookup`) para IP/CIDR/ASN:
  - best path, AS-Path, Origin, LocalPref, MED, NextHop;
  - communities (standard/ext/large);
  - detecção de prepend;
  - leitura de `Advertised to such XX peers` e cruzamento com peers marcados como Operadora/IX;
  - checagem de `advertised-routes`, AS-Path e prepend por peer marcado.

### Dashboard operacional

- Aba `Dashboard` com lista de dispositivos cadastrados.
- Status por dispositivo: conectividade SSH e coleta SNMP.
- Ações rápidas: `Testar SSH` e `Coletar SNMP`.

### Logs e observabilidade

- Logs persistidos em arquivo com rotação por tamanho e compressão (`.gz`).
- Stream de eventos estruturados para consumo da interface.
- Endpoint de leitura recente: `GET /api/logs/recent?limit=100`.
- Aba de log apresenta os últimos 100 eventos (incluindo falhas de módulos).
- Aba de log suporta filtros por nível (error/warn/info/success) e por origem (STARTUP/SNMP/SSH/etc.).

### Validação de acessibilidade no startup

- Ao iniciar a aplicação, é executada validação automática por dispositivo:
  - teste de conectividade SSH;
  - teste de acessibilidade SNMP.
- Resultados são registrados no log com origem `STARTUP`.

### Interfaces

- IPv4 exibido em notação CIDR.
- Coluna de IPv6 por interface.
- Preparação de correlação interface ↔ peering por sub-rede (`related_peers` no endpoint).
- Interface ausente na coleta é exibida como inativa (tachada) e pode ser desativada manualmente sem remoção física.
- Interfaces legadas no padrão `nome(40G)` são limpas automaticamente quando o mesmo `nome` base existe na coleta atual.

### Estado ativo/inativo de inventário

- `interfaces` e `bgp_peers` possuem `is_active` e `deactivated_at`.
- Ações de "Remover" na UI executam desativação lógica (mantendo histórico no banco).
- Endpoints:
  - `PATCH /api/devices/{id}/interfaces/{interface_id}/deactivate`
  - `PATCH /api/devices/{id}/bgp-peers/{peer_id}/deactivate`

## Stack tecnológica

| Camada | Tecnologia |
|---|---|
| Backend API | FastAPI |
| ORM | SQLAlchemy 2.x async |
| Banco | configurável via `DATABASE_URL` (PostgreSQL ou SQLite) |
| SSH | Netmiko 4.x |
| SNMP | pysnmp |
| Frontend | React + Vite |
| UI | Tailwind CSS + Lucide |
| Criptografia | Fernet (`cryptography`) |

## Arquitetura por módulos

### Backend (`backend/app`)

### Núcleo

- `main.py`: bootstrap da API, CORS, `lifespan`, inclusão de routers e healthcheck.
- `config.py`: leitura de variáveis de ambiente.
- `database.py`: engine async, session factory, patches de schema e `get_db`.
- `models.py`: modelos ORM.
- `schemas.py`: schemas de entrada/saída da API.
- `crypto.py`: criptografia/descriptografia de credenciais.
- `activity_log.py`: padronização de logs para UI/backend.

### Routers

- `routers/devices.py`
  - CRUD de dispositivos.
  - Teste de conexão SSH.
  - Coleta SSH Huawei (`/ssh/collect-huawei`).
  - Investigação BGP export por SSH (`/ssh/bgp-export-lookup`).
- `routers/snmp.py`
  - Coleta SNMP completa e status-refresh.
  - Listagem de interfaces, peers BGP e histórico de inventário.
  - Atualização de papel de peer BGP.
  - Endpoints live (coleta sem persistência).
- `routers/logs.py`
  - API de leitura dos últimos eventos para a aba de log.

### Serviços

- `services/huawei_ssh_inventory.py`: coleta VRP via Netmiko e persistência de inventário.
- `services/bgp_export_lookup.py`: fluxo de investigação de prefixo/ASN por comandos `display bgp`.
- `services/snmp_inventory.py`: coleta SNMP completa + persistência com diff.
- `services/snmp_status_refresh.py`: atualização leve de status/counters.
- `services/inventory_persist.py`: funções de persistência e classificação (incluindo iBGP/eBGP e papéis).
- `services/inventory_history.py`: registro de eventos de mudança de inventário.
- `services/startup_checks.py`: validação automática de SSH/SNMP no startup.

### Parsing Huawei VRP

- `huawei_cli/collector.py`: execução de comandos de coleta.
- `huawei_cli/adapter.py`: montagem de payload de inventário.
- `huawei_cli/parsers_if.py`: parser de interfaces.
- `huawei_cli/parsers_bgp.py`: parser de peers BGP.
- `huawei_cli/parsers_vrf.py`: parser de VRFs.

### Frontend (`frontend/src`)

### Composição e navegação

- `App.jsx`: orquestra layout, navegação e polling SNMP.
- `components/Sidebar.jsx`: menu lateral.
- `components/DeviceTree.jsx`: árvore de dispositivos + subviews.
- `components/Header.jsx`: breadcrumbs.

### Páginas

- `pages/DevicesPage.jsx`: listagem/cadastro/edição/exclusão/teste de conexão.
- `pages/DashboardPage.jsx`: visão operacional de dispositivos e ações de atualização.
- `pages/InterfacesPanel.jsx`: tabela de interfaces, filtros e coleta manual.
- `pages/BGPPanel.jsx`: tabela de peers, filtros avançados e classificação Cliente/Operadora/IX/CDN.
- `pages/BgpLookupPanel.jsx`: investigação de prefixo/ASN (view principal de consulta).
- `pages/FiltrosPanel.jsx`: painel auxiliar.
- `pages/LogPanel.jsx`: visão dos logs operacionais.

### APIs e utilitários

- `api/devices.js`: chamadas HTTP de dispositivos e lookup BGP.
- `api/snmp.js`: chamadas HTTP de SNMP e BGP peers.
- `context/LogContext.jsx`: contexto global de logs.
- `utils/reportBackendLog.js`: normalização de logs de backend para UI.

## Endpoints principais

### Dispositivos

- `GET /api/devices/`
- `POST /api/devices/`
- `GET /api/devices/{id}`
- `PUT /api/devices/{id}`
- `DELETE /api/devices/{id}`
- `POST /api/devices/{id}/test-connection`
- `POST /api/devices/{id}/ssh/collect-huawei`
- `POST /api/devices/{id}/ssh/bgp-export-lookup`
- `POST /api/devices/{id}/bgp/provider-advertised-routes` — prefixos advertidos ao peer **Operadora** (SSH Huawei; router SNMP; paginação 20; máx. 100)

### SNMP e inventário

- `POST /api/devices/{id}/snmp/collect`
- `POST /api/devices/{id}/snmp/status-refresh`
- `GET /api/devices/{id}/interfaces`
- `GET /api/devices/{id}/bgp-peers`
- `PATCH /api/devices/{id}/bgp-peers/{peer_id}`
- `GET /api/devices/{id}/inventory-history`
- `GET /api/devices/{id}/snmp/interfaces/live`
- `GET /api/devices/{id}/snmp/bgp/live`

## Modelo de dados (resumo)

- `devices`: inventário base e credenciais.
- `interfaces`: snapshot de interfaces por dispositivo.
- `bgp_peers`: peers BGP + papel (Cliente/Operadora/IX/CDN) + marcador iBGP.
- `device_vrfs`: VRFs detectadas por dispositivo.
- `inventory_history`: histórico de alterações (insert/update/delete + mudanças manuais relevantes).
- `configurations` e `interface_metrics`: estrutura de evolução/telemetria.

## Notas operacionais importantes

- Senhas SSH ficam criptografadas (Fernet) e são decriptadas apenas em runtime.
- Polling automático SNMP foi separado em coleta completa e refresh leve para reduzir impacto.
- Para evitar esgotamento de conexões em cenários com polling frequente em SQLite, o engine está com `NullPool`.
- SSH de investigação BGP recebeu ajuste de timeout e `fast_cli=False` para cenários Huawei com sessão mais lenta.

## Diretriz sobre `00-Material`

- O conteúdo em `./00-Material/*` é **referência** (consulta, ideias e base de reaproveitamento).
- O código oficial e mantido da aplicação deve existir em `backend/` e `frontend/`.
- Qualquer adaptação vinda de `00-Material` deve ser refatorada e integrada aos módulos oficiais antes de uso em produção.

## Diretriz de documentação contínua

- Toda mudança funcional deve ser registrada em `CHANGELOG.md`.
- Sempre refletir mudanças relevantes também em `docs/context.md` e `docs/roadmap.md`.

## Análise: prefixos BGP advertidos a operadoras via SNMP

### Situação atual no código

- `snmp_collector.collect_bgp` usa apenas **BGP4-MIB** (`1.3.6.1.2.1.15`): estado da sessão, AS remoto, endereço local, `bgpPeerInUpdates` / `bgpPeerOutUpdates` (`.3.1.11` / `.3.1.12` — **número de mensagens UPDATE** recebidas/enviadas, **não** lista nem contagem de prefixos NLRI). **Não** há walk de prefixo/CIDR **por peer**.
- Para export / “advertised routes” detalhado ao peer, o fluxo confiável hoje é **SSH** (ex.: `bgp_export_lookup` com `display bgp … advertised-routes` — confirmado no código em `bgp_export_lookup.py` / logs de atividade).

### Verificação (repositório)

- **SNMP:** somente OIDs em `backend/app/snmp_collector.py` (`OID` + `collect_bgp`); nenhuma referência a `hwBgp`, `2011` ou tabela de rotas advertidas por peer no backend.
- **Prefixos advertidos:** obtidos por **CLI** (não SNMP), conforme uso de `advertised-routes` nos fluxos SSH existentes.

### É possível coletar a lista de prefixos advertidos só com SNMP?

- **MIB padrão BGP4-MIB (RFC 1657 / uso típico):** **não** oferece uma tabela portável do tipo “para o peer *P*, estes são todos os prefixos enviados no Adj-RIB-Out após policy”. O que existe são objetos de **vizinhança** e, em outras ramificações da árvore BGP, informações de **caminho/atributo** que **não** mapeiam de forma estável e vendor-neutra para “anunciado ao eBGP *X*”.
- **Huawei VRP / NE8000:** a lista fina costuma aparecer no **CLI** ou em **NETCONF/YANG**. A Huawei publica MIBs proprietários sob `1.3.6.1.4.1.2011` (família `hwBgp*` / VPN-BGP); **pode** existir tabela indexada por instância BGP + peer + prefixo, mas **depende de versão de VRP, modelo e o que o agente SNMP expõe**. Só dá para afirmar após **snmpwalk** no equipamento real contra o peer operadora (global e, se aplicável, `vpn-instance`).
- **Conclusão prática:** para **operadoras**, tratar SNMP como **possível só após validação de OID** no lab/produção; até lá, considerar **não garantido** e manter **SSH (ou NETCONF)** como fonte da lista de prefixos.

### Paginação (>20) e teto (>100)

- Em SNMP, “paginar” um walk é **cortar por lotes** (GETBULK com `max-repetitions`, ou parar após *N* varbinds) usando o **último OID** como próximo índice — não é paginação estilo REST.
- As regras pedidas são **viáveis na aplicação**:
  - **Até 20** prefixos: retornar primeira “página” (primeiros 20) se quiser UI incremental.
  - **Mais de 100:** interromper a coleta, **não** persistir lista completa e devolver aviso explícito (ex.: *“Muitas rotas advertidas (>100); não coletado via SNMP — use investigação SSH ou NETCONF.”*).

### Recomendação de produto

- **SNMP:** sessão, AS, contadores, eventualmente **contagem** se no futuro existir OID escalar confiável por peer.
- **Lista de prefixos para operadora:** **SSH** (já alinhado ao VRP) ou **NETCONF**; reservar SNMP para essa lista **só** após documentar OIDs válidos no NE8000 em uso.
