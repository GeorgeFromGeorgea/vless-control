# Intended interaction model (implementation roadmap)

- Admin-only Telegram bot, with numeric IDs allowlisted; missing allowlist denies all actions.
- Issue individual: create UUID, attach selected profiles, return separate import links with a warning that they are credentials.
- Issue shared: a shared label/profile may be offered, but individual UUIDs remain the safer default; any truly shared identity requires explicit policy.
- User selects "does not connect" and chooses a profile to try. The bot explains that server-side checks cannot establish carrier/device reachability.
- Revoke means disabling the UUID on Xray and marking the registry identity inactive; transactional config update, backup, validation, controlled restart, rollback on error.

No automatic port hopping: VLESS clients generally need a new imported link/config, and remote server probes cannot test a particular subscriber's route.
