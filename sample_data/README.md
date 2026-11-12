# Sample data

Everything in this folder is **fictional**. The owners are Household A and
Household B, the figures and account references are invented, and the
broker layouts are simplified to what the importers read. None of it comes
from a real account.

## What is here

| File | Broker / source | Loaded by |
| --- | --- | --- |
| `fidelity/fidelity_llc_sample.csv` | Fidelity activity export (LLC account) | `import_fidelity_csv` |
| `hargreaves_lansdown/HL_history_sample.csv` | Hargreaves Lansdown transaction history | `import_hl_csv` |
| `aussuper/AusSuperAG_sample.csv` | AusSuper transaction export (AG account) | `import_aussuper_csv` |
| `statements/fidelity/ACCOUNT_B/Statement9302026.pdf` | Fidelity monthly statement, owner-B account | `import_fidelity_statements` |
| `statements/aussuper/AusSuper2026JUNST.pdf` | AusSuper annual statement, year to 30 June 2026 | `import_aussuper_statements` |

The `ACCOUNT_B` folder name tells the Fidelity statement importer which
account a statement belongs to. The `2026JUNST` part of the AusSuper file name
gives the snapshot date.

## Load them into a demo database

From the project root:

```bash
python sample_data/seed_demo_db.py                 # writes data/demo.sqlite3
STORAGE_DB_PATH=data/demo.sqlite3 python -m web    # then open http://localhost:8080
```

The script deletes and rebuilds the database at the path it writes to, so
point it only at a demo file. It never touches the main database.

The result is four funds across two households: Fidelity and UK Super for
Household A, Super A for Household A, and Fidelity Account B for Household B.
There are 14 trades, plus cash events and statement snapshots.

## Regenerate the statement PDFs

The PDFs are generated with reportlab, which is listed in `requirements-dev.txt`
and is not needed to run the app:

```bash
pip install -r requirements-dev.txt
python sample_data/make_sample_pdfs.py
```

The generated layout includes only the lines the statement parsers look for.
It is not a copy of any real statement.

## Using your own exports

Use the same layouts with the matching `main.py` flag, and keep real files out
of this folder and out of git:

```bash
python main.py --import-fidelity-csv path/to/activity.csv
python main.py --import-hl-csv path/to/HL_history.csv
python main.py --import-aussuper-csv path/to/AusSuperAG.csv
python main.py --import-fidelity-statements path/to/Statement9302026.pdf
python main.py --import-aussuper-statements path/to/AusSuper2026JUNST.pdf
```
