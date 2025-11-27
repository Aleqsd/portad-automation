# Portad Dashboard Fetcher

Script minimal pour récupérer le tableau de bord LAYA, le parser et produire un snapshot JSON, avec notification Pushover en cas de changement.

## Prérequis
- Python 3.10+  
- Accès réseau vers `https://portad.laya.fr/`

## Installation rapide
```bash
python3 -m venv .venv
. .venv/bin/activate
pip install requests beautifulsoup4
```

## Configuration
Crée un fichier `.env` (non versionné) :
```
PORTAD_USER=ton_mail
PORTAD_PASS=ton_mot_de_passe
PUSHOVER_USER_KEY=ta_cle_user    # optionnel
PUSHOVER_API_TOKEN=ta_app_token  # optionnel
```

## Usage
```bash
.venv/bin/python fetch_portad_dashboard.py
```
- Chaque exécution :
  - Se connecte une fois.
  - Récupère les données du tableau de bord (incl. Relevé de compte porté, onglets “Mes documents”, etc.).
  - Sauvegarde un JSON daté dans `snapshots/` et met à jour `snapshots/last_snapshot.json`.
  - Compare avec le snapshot précédent ; si différence, envoie une notification Pushover (si clés définies).
  - Affiche le JSON courant sur stdout.

## Dossiers/outputs
- `snapshots/` (ignoré par git) : archives locales des runs, contient des données personnelles.
  - `last_snapshot.json` : JSON prettifié pour comparer facilement.
  - `portad-dashboard-YYYYMMDD-HHMMSS.json.gz` : snapshot horodaté compressé (écrit uniquement si changement détecté).
- `fetch_portad_dashboard.py` : script principal.

## Sécurité / bonnes pratiques
- Ne pas committer `.env` ni `snapshots/` (données sensibles).
- Renouveler le mot de passe dans `.env` dès que nécessaire.
- Pushover est facultatif ; sans clés, aucune alerte n’est envoyée.

## Commandes utiles
- Relancer proprement : `rm -rf .venv && python3 -m venv .venv && . .venv/bin/activate && pip install requests beautifulsoup4`
- Exécuter en silencieux (mais garde notifications) : idem, le script n’a pas d’options supplémentaires.
