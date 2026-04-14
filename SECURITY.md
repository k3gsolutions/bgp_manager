# Política de segurança

## Reportar vulnerabilidades

Se descobrir um problema de segurança, contacte os mantenedores do repositório de forma **privada** (não abra issue pública com exploit ou credenciais).

Inclua: passos para reproduzir, impacto estimado e versão/commit.

## Superfície sensível

- **Credenciais de equipamentos** são guardadas cifradas (Fernet); a chave `FERNET_KEY` deve ser segredo de ambiente.
- **JWT**: com `APP_ENV=production`, o arranque falha se `JWT_SECRET` for fraco ou estiver em falta.
- **Auditoria / logs** em `backend/logs/` podem conter IPs e metadados de operações — não publique esse diretório.

## Boas práticas de implantação

1. `APP_ENV=production`, `JWT_SECRET` longo e aleatório, `FERNET_KEY` gerada uma vez por ambiente.
2. HTTPS no tráfego utilizador ↔ API; cookies não são usados para API (Bearer), mas proteja o canal.
3. Restrinja `CORS_EXTRA_ORIGINS` às origens do frontend em produção.
4. Utilizadores com papel mínimo necessário (RBAC); operações destrutivas de BGP inativos exigem **superadmin** na API.

## Riscos conhecidos / limitações

- O backend descriptografa senhas SSH para sessões Netmiko (necessidade funcional); quem acede ao processo + `FERNET_KEY` pode aceder às credenciais.
- Não há rate limiting embutido no login; recomenda-se reverse proxy (nginx, cloudflare) em exposição pública.
