# AGENTS

## Mission
- Récupérer le tableau de bord LAYA avec `fetch_portad_dashboard.py`.
- Stocker un snapshot JSON daté et comparer avec le précédent.
- Alerter via Pushover si une différence est détectée.

## Inputs attendus
- `.env` (non versionné) :
  - `PORTAD_USER`, `PORTAD_PASS` (obligatoire)
  - `PUSHOVER_USER_KEY`, `PUSHOVER_API_TOKEN` (optionnel pour notifications)

## Procédure standard
1. Activer l’environnement : `. .venv/bin/activate`
2. Lancer : `.venv/bin/python fetch_portad_dashboard.py`
3. Consulter les snapshots dans `snapshots/` (ignoré par git) :
   - `last_snapshot.json` lisible pour les diffs
   - snapshots datés compressés `.json.gz` pour gagner de la place

## Points d’attention
- Ne jamais committer `.env` ni `snapshots/` (données personnelles).
- Le script fait un seul login par exécution.
- Notifications silencieuses si les variables Pushover sont absentes.
