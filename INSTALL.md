# Installation / Déploiement (Ubuntu)

Supposons une installation dans `/opt/portad-automation`.

## 1) Prérequis

```bash
sudo apt-get update
sudo apt-get install -y python3-venv git
```

## 2) Cloner et préparer l’environnement

```bash
cd /opt
git clone <ton_repo> portad-automation
cd portad-automation
make venv
make install
```

## 3) Créer le fichier `.env`

```bash
cat > .env <<'EOF'
PORTAD_USER=email
PORTAD_PASS=passwpord
PUSHOVER_USER_KEY=ta_cle_user    # optionnel
PUSHOVER_API_TOKEN=ta_app_token  # optionnel
EOF
chmod 600 .env
```

## 4) Test manuel

```bash
make run > run.log
tail -n 5 run.log        # vérifier l’absence d’erreur
ls snapshots             # last_snapshot.json + .json.gz si diff détectée
```

## 5) Cron horaire (minute 5)

Installer :

```bash
make cron-install
```

La ligne cron si besoin de copier/coller :

```bash
( crontab -l 2>/dev/null | grep -v "fetch_portad_dashboard.py"; echo "5 * * * * cd /opt/portad-automation && /usr/bin/env bash -lc 'set -a; . .env; set +a; . .venv/bin/activate; .venv/bin/python fetch_portad_dashboard.py >> cron.log 2>&1'" ) | crontab -
```

Vérifier :

```bash
crontab -l
```

Retirer l’entrée cron :

```bash
make cron-remove
```

## 6) Mises à jour ultérieures

```bash
make pull     # git pull
make update   # git pull + reinstall deps (requirements.txt)
```

## 7) Notes

- Snapshots et logs restent dans `/opt/portad-automation`.
- `cron.log` grossira avec le temps : ajouter un logrotate si besoin.
- Les snapshots datés `.json.gz` ne sont créés qu’en cas de différence ; `last_snapshot.json` reste lisible pour comparer.
