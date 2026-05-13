# make-usage-dashboard

Dashboard qui analyse la consommation Make.com (opérations, coûts estimés, erreurs / avertissements). Fonctionne en local **et** en déploiement automatique sur GitHub Pages (rafraîchi toutes les 6h).

## Pré-requis

- Python 3.10+
- Un compte Make.com avec un token API personnel et l'ID de ton organisation
- (pour le déploiement) un compte GitHub avec un repo public

## Installation locale

```powershell
# 1. Créer un environnement virtuel Python
python -m venv .venv

# 2. L'activer (Windows / PowerShell)
.\.venv\Scripts\Activate.ps1

# 3. Installer les dépendances
pip install -r requirements.txt
```

Si PowerShell refuse d'activer le venv avec un message sur `ExecutionPolicy` :

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

## Configuration

Si tu n'as pas encore de `.env`, copie le template :

```powershell
Copy-Item .env.example .env
notepad .env
```

Variables **requises** :
- `MAKE_API_TOKEN` — token généré dans Make.com → *Profile → API*
- `MAKE_API_BASE_URL` — ex. `https://eu2.make.com/api/v2` (selon ta zone)
- `MAKE_ORGANIZATION_ID` — visible dans l'URL de ton organisation

Variables **optionnelles** :
- `MAKE_TEAM_ID` — si vide ou absent, le script utilise `organizationId` comme scope et tente d'auto-découvrir une seule team dans l'organisation pour les folders. Si plusieurs teams existent, les scénarios sont regroupés sous *« Sans folder / org-level »*.
- `MAKE_PLAN_CREDITS`, `MAKE_MONTHLY_COST_EUR` — pour le calcul du coût estimé
- `MAKE_EXTRA_CREDITS`, `MAKE_EXTRA_COST_EUR` — surconsommation éventuelle
- `CURRENCY` — devise affichée (défaut `EUR`)

> 💡 Toutes les variables d'environnement sont normalisées au chargement : espaces et guillemets entourants sont retirés, et les valeurs `""`, `none`, `null`, `undefined` (insensible à la casse) sont traitées comme vides. Utile notamment pour `MAKE_TEAM_ID` : un secret GitHub non renseigné est parfois injecté comme la chaîne littérale `""`, ce qui produirait un appel API invalide (`teamId=%22%22`).

> ⚠ Le fichier `.env` est exclu de Git (voir `.gitignore`). **Ne le commite jamais.**

## Utilisation locale

```powershell
# 1. Récupérer les données depuis l'API Make.com
python scripts\fetch_make_usage.py
# → écrit data\make_usage.json + résumé à l'écran

# 2. Lancer un serveur HTTP local (depuis la racine du projet)
python -m http.server 8000

# 3. Ouvrir le dashboard dans le navigateur
# http://localhost:8000/dashboard/
```

Pourquoi un serveur HTTP ? Le dashboard charge `data/make_usage.json` via `fetch()` et les navigateurs bloquent ce type de requête en `file://`. `python -m http.server` est la solution la plus simple — pas d'installation supplémentaire.

## Déploiement gratuit sur GitHub Pages

Le projet inclut un workflow GitHub Actions (`.github/workflows/fetch-and-deploy.yml`) qui :
- se lance **toutes les 6 heures** (cron), à chaque push sur `main`, et à la demande
- installe Python, lance `scripts/fetch_make_usage.py` avec les **secrets GitHub** comme variables d'environnement
- publie `dashboard/` + `data/` sur GitHub Pages

### Étape 1 — pousser le projet sur GitHub

```powershell
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<TON_USER>/make-usage-dashboard.git
git push -u origin main
```

⚠ Avant `git add .`, lance `git status` et vérifie que **`.env` n'apparaît pas** dans la liste. Il est exclu via `.gitignore`, mais une vérification ne coûte rien.

### Étape 2 — activer GitHub Pages

1. Va dans le repo → **Settings → Pages**
2. Sous **Source**, choisis **GitHub Actions** (pas "Deploy from a branch")
3. Sauvegarde

### Étape 3 — ajouter les secrets

**Settings → Secrets and variables → Actions → New repository secret**. Ajoute :

| Secret | Requis | Description |
|---|---|---|
| `MAKE_API_TOKEN` | ✅ | Token API Make.com |
| `MAKE_API_BASE_URL` | ✅ | Ex. `https://eu2.make.com/api/v2` |
| `MAKE_ORGANIZATION_ID` | ✅ | ID numérique de l'organisation |
| `MAKE_TEAM_ID` | ❌ | Optionnel — laisser vide (ou ne pas créer le secret) pour utiliser `organizationId` + auto-discovery |
| `MAKE_PLAN_CREDITS` | ❌ | Crédits inclus dans le forfait |
| `MAKE_MONTHLY_COST_EUR` | ❌ | Coût mensuel du forfait |
| `MAKE_EXTRA_CREDITS` | ❌ | Crédits supplémentaires achetés |
| `MAKE_EXTRA_COST_EUR` | ❌ | Coût des crédits supplémentaires |
| `CURRENCY` | ❌ | Défaut `EUR` |

> Les secrets sont chiffrés par GitHub et invisibles dans les logs Actions. Le script ne les affiche jamais.

### Étape 4 — déclencher le premier build

**Actions → "Refresh Make.com usage and deploy dashboard" → Run workflow** (bouton à droite).

Au bout de ~1-2 min, ton dashboard est en ligne à :

```
https://<TON_USER>.github.io/make-usage-dashboard/
```

L'URL définitive apparaît aussi dans l'onglet Actions du job déployé.

### ⚠ Confidentialité — le repo est public

Sur un repo public, **`data/make_usage.json` est lisible par tout le monde** (par toute personne qui connaît l'URL). Il contient :

- ✅ Inclus dans le JSON public : **noms** des scénarios et folders, **opérations consommées**, **coûts estimés**, nombre d'erreurs / avertissements, % de quota
- ❌ Jamais inclus : **ton token API**, **l'ID organisation**, l'**ID des scénarios**, l'**ID des teams** (le script les utilise mais ne les écrit pas dans le JSON)

Si les noms de tes scénarios sont sensibles, garde le repo en privé. GitHub Pages gratuit ne fonctionne **que** sur les repos publics — pour Pages sur repo privé, il faut un plan GitHub Pro/Team.

## Que fait le script ?

`scripts/fetch_make_usage.py` :

1. Charge les variables d'environnement via `python-dotenv` (depuis `.env` en local, depuis les secrets en CI). Le token reste en mémoire uniquement.
2. Détecte le scope : `teamId` si `MAKE_TEAM_ID` est fourni, sinon `organizationId` avec auto-discovery d'une éventuelle team unique pour les folders.
3. Récupère (en paginé) les scénarios, folders, l'usage org sur 30 jours, et les logs d'exécution par scénario sur 30 jours.
4. Calcule par scénario et par folder : opérations 7j / 30j, coût 7j / 30j en EUR, erreurs, warnings.
5. Écrit `data/make_usage.json`. Toute erreur d'API est capturée et listée dans la clé `notes` du JSON — le script ne s'arrête pas.

## Structure du projet

```
make-usage-dashboard/
├── .github/
│   └── workflows/
│       └── fetch-and-deploy.yml    # CI : refresh toutes les 6h + déploiement Pages
├── scripts/
│   ├── fetch_make_usage.py         # Aspire les données Make.com → JSON
│   └── test_api.ps1                # Test rapide du token (PowerShell)
├── dashboard/
│   └── index.html                  # Dashboard statique (HTML + Chart.js via CDN)
├── data/
│   └── make_usage.json             # Données générées (gitignored — généré en CI)
├── .claude/skills/...              # Skill Claude Code (contexte projet)
├── .env                            # Secrets locaux (gitignored)
├── .env.example                    # Template
├── .gitignore
├── requirements.txt
└── README.md
```

## Roadmap V2

- Détection automatique des scénarios coûteux + alertes (email, Slack)
- Historique : conserver des snapshots datés pour suivre l'évolution semaine après semaine
- Filtrage par folder dans le dashboard (tabs / dropdown)
