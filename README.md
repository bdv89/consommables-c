# Inventaire consommable bâtiment C

Application web d'inventaire des **consommables du bâtiment C** (EPI, verrerie, seringues,
emballages, adhésifs…) : 87 articles, quantités suivies, journal des mouvements, alerte de
réapprovisionnement.

**En production : <http://mn-panda/consommable_batiment_c/>**

> 📄 Documentation complète : **[docs/inventaire-consommable-batiment-c.md](docs/inventaire-consommable-batiment-c.md)**
> · Guide de contribution : [CLAUDE.md](CLAUDE.md)

## Démarrage rapide

En production, l'app tourne comme service Windows `InventoryBatimentC` (NSSM, port 8003),
exposée par Nginx sous `/consommable_batiment_c/`. Une tâche planifiée
`BatimentCLowStockAlert` envoie chaque matin le digest « stock bas ».

Pour un lancement local (développement) :

```bash
cd server_display
venv\Scripts\python run.py --db consommable_batiment_c.sqlite --port 8042
```

Puis ouvrir <http://127.0.0.1:8042/>. ⚠️ Ne pas utiliser 8003 : c'est le port du service.

## Ce que fait l'application

- **Inventaire** — 1 ligne = 1 article avec sa quantité. Recherche instantanée (désignation,
  référence, catégorie, emplacement), colonnes affichables à la carte, mode sombre.
- **Sortie / réappro en un clic** — la quantité est elle-même le bouton : `[−] 33 [+]`.
  Cliquer sur le nombre permet de saisir un recomptage.
- **Journal des mouvements** — chaque changement de stock est tracé (date, article, ±, stock
  après, motif, poste). Le journal ne se modifie pas.
- **Seuil par article** — sous le seuil, l'article est signalé dans la liste et remonte dans
  le digest e-mail quotidien. *(Un seuil à `0` = pas d'alerte.)*
- **Photo par article** — pour lever le doute sur « quel écouvillon ? ».
- **Export CSV** de la sélection courante — dont la liste de réappro, via le filtre
  « Sous seuil ».

## Origine des données

`Inventaire consomato.xlsx`, onglet `inventaire Mezzanine` (3 colonnes :
Quantité, Description, Référence). L'import déduit l'unité, le conditionnement, la catégorie
et le fournisseur, puis affiche un **rapport** des cas qu'il n'a pas su trancher.

```bash
# Depuis la RACINE du dépôt
python server_display/scripts/import_consommables_xlsx.py \
    --xlsx "Inventaire consomato.xlsx" --sheet "inventaire Mezzanine" \
    --db server_display/consommable_batiment_c.sqlite
```

⚠️ L'import est **destructif** : après le premier chargement, la source de vérité est
l'application, pas le fichier Excel.

## Tests

```bash
cd server_display
venv\Scripts\python -m pytest tests/
```

## Lignée

Ce dépôt est un fork de l'**inventaire de cibles** (`../inventaire_cibles/`, en production
sur `http://mn-panda/cibles/`), lui-même issu d'un outil d'OCR d'étiquettes chimiques. Le
socle serveur (FastAPI + SQLite + FTS5 + UI) est réutilisé ; le modèle « 1 ligne = 1 objet
physique » a été remplacé par une gestion de quantités avec journal de mouvements.

## Reste à faire

1. ⏳ **Renommer le dossier** `consommables_chips` → `consommable_batiment_c`. Tout le reste
   porte déjà le nouveau nom ; le service et le wrapper d'alerte ont un repli temporaire sur
   l'ancien chemin. Un dossier ne peut pas être renommé tant qu'un processus l'a comme
   répertoire courant : **fermer la session Claude Code**, puis, depuis un PowerShell élevé
   situé ailleurs :
   ```powershell
   & 'D:\restart\00_Le_Code\06_unified_work\serveur_automate\deploy\Rename-And-Deploy-BatimentC.ps1'
   ```
2. 🔴 **Poser les premiers seuils.** Aucun article n'a de `seuil_min` : la tâche de 08:00
   tourne et ne signalera jamais rien (`0` = alerte désactivée, il faut `≥ 1`).
3. **Trancher la revue d'import** : le « 800 sachet de 100 » de `CONS-025`, et les 11 lignes
   à promouvoir en boîte/sachet.
