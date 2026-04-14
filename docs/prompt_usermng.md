Você vai implementar **gestão de usuários com controle de acesso multiempresa** no projeto atual **BGP Manager**, sem criar projeto paralelo e sem quebrar o fluxo existente.

## Objetivo

Criar autenticação, usuários, empresas, permissões e escopo de acesso para que cada usuário só veja/edite as empresas autorizadas e os dispositivos dessas empresas.

## O que implementar

### Backend

Adicionar:

* autenticação com JWT
* entidade `users`
* entidade `companies`
* vínculo `user_companies`
* ajuste em `devices` para incluir `company_id`
* perfis:

  * `superadmin`
  * `admin`
  * `operator`
  * `viewer`

Permissões mínimas:

* `users.view/create/edit/delete`
* `companies.view/create/edit/delete`
* `devices.view/create/edit/delete`
* `devices.test_connection`
* `devices.snmp_collect`
* `devices.snmp_refresh`
* `devices.ssh_collect`
* `bgp.view`
* `bgp.edit_role`
* `bgp.lookup`
* `interfaces.view`
* `logs.view`

Criar routers:

* `auth.py`
* `users.py`
* `companies.py`

Criar endpoints mínimos:

* `POST /api/auth/login`
* `GET /api/auth/me`
* `GET /api/users`
* `POST /api/users`
* `GET /api/users/{id}`
* `PUT /api/users/{id}`
* `DELETE /api/users/{id}`
* `PATCH /api/users/{id}/companies`
* `PATCH /api/users/{id}/password`
* `GET /api/companies`
* `POST /api/companies`
* `GET /api/companies/{id}`
* `PUT /api/companies/{id}`
* `DELETE /api/companies/{id}`

Criar dependências reutilizáveis de segurança:

* `get_current_user`
* `require_permission(...)`
* `require_company_access(...)`
* helper para filtrar queries por escopo do usuário

Aplicar controle de acesso em tudo que já existe:

* dispositivos
* bgp
* interfaces
* logs
* inventory history
* ssh test
* snmp collect
* snmp refresh
* ssh collect
* bgp lookup

Regra:

* `superadmin` acessa tudo
* demais usuários só acessam empresas permitidas
* backend é a fonte da verdade

### Banco

Criar migração/bootstrapping compatível com o projeto atual.

Criar seed inicial com:

* 1 empresa padrão
* 1 usuário `superadmin`
* permissões/perfis base

Senha com hash seguro.
JWT com expiração.
Sem hardcode inseguro.

### Frontend

Implementar:

* tela de login
* `AuthContext`
* proteção de rotas
* controle visual por permissão
* páginas:

  * `UsersPage.jsx`
  * `CompaniesPage.jsx`

Na UI:

* esconder menus sem permissão
* esconder ações sem permissão
* listar apenas empresas autorizadas
* listar apenas dispositivos dentro do escopo permitido
* mostrar empresa do dispositivo
* no cadastro/edição de dispositivo, permitir apenas empresas autorizadas

## Regras de negócio

* todo dispositivo deve pertencer a uma empresa
* usuário vê apenas empresas permitidas
* usuário vê apenas dispositivos das empresas permitidas
* edição depende do perfil/permissão
* estrutura deve ficar pronta para futura evolução para override por dispositivo, mas sem complicar a primeira entrega

## Arquivos e documentação

Atualizar o projeto real em `backend/` e `frontend/`.
Não usar `00-Material` como código oficial.

Ao final:

* atualizar `CHANGELOG.md`
* atualizar `docs/context.md`
* atualizar `docs/roadmap.md`

## Critérios de aceite

A entrega só termina quando existir:

1. login funcional
2. `/api/auth/me` funcional
3. CRUD de empresas
4. CRUD de usuários
5. vínculo usuário ↔ empresas
6. perfis/permissões funcionando
7. dispositivos filtrados por empresa
8. UI respeitando permissões
9. backend bloqueando acesso indevido
10. seed inicial
11. documentação atualizada

## Entrega final esperada

Quando concluir, me entregue:

1. resumo técnico
2. lista de arquivos criados/alterados
3. instruções objetivas para subir
4. pontos preparados para evolução futura

Trabalhe sobre a base atual do projeto BGP Manager descrita no contexto fornecido. Respeite a arquitetura existente em FastAPI + SQLAlchemy async + React/Vite. Contexto base do projeto:  e arquitetura/módulos atuais: 

Não quero só sugestão. Quero implementação.
