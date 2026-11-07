# Firefly Ingest

Ingestione rapida di estratti conto (CSV/XLSX/XLS) e spese al volo in [Firefly III](https://www.firefly-iii.org/),
con revisione prima dell'invio, deduplica e regole locali. Web app React + bot Telegram in un unico container.

## Come funziona

```
file / testo / Telegram
        │
        ▼
 profilo banca ──► normalizzazione (data, importo con segno, descrizione)
                         │
                         ▼
                   deduplica  ── hash locale + external_id + confronto con Firefly (stessa data ±1g, stesso importo)
                         │
                         ▼
                   regole locali ── categoria, budget, tag, controparte, giroconto
                         │
                         ▼
                   revisione (web o bot) ──► POST /api/v1/transactions
```

- **Profili banca**: al primo file di una banca la web app mostra le colonne e ti fa scegliere data, importo
  (unico o uscite/entrate), descrizione e controparte. I file successivi vengono riconosciuti in automatico dalle intestazioni.
- **Regole**: corrispondenza su descrizione, controparte o note (contiene / inizia con / uguale / regex), con filtri su
  direzione, conto e importo. Si applicano in ordine di priorità e ogni regola riempie solo i campi ancora vuoti.
  Dalla revisione, "+ regola" crea una regola dalla riga e la riapplica al lotto.
- **Deduplica**:
  - `duplicata`: la riga è già stata importata da questa app (stesso hash), oppure Firefly ha già l'`external_id`.
  - `possibile duplicato`: in Firefly c'è già un movimento con lo stesso importo sullo stesso conto (per esempio inserito a mano).
    Non viene selezionata in automatico.
  - Firefly riceve `error_if_duplicate_hash: true`, quindi rifiuta comunque i doppioni esatti.
- **Inserimento rapido** (web e Telegram), una transazione per riga:
  ```
  12.50 pizza ieri @carta #cene !amici
  +1800 stipendio 27/10
  3,20 caffè
  ```
  `+` = entrata · data: `oggi`, `ieri`, `altroieri`, `lunedì`…, `3/11`, `03/11/2026` · `@conto`, `#categoria`, `!tag`.
  Gli alias si configurano in Impostazioni (es. `@carta` → "Carta Visa").

## Configurazione

| Variabile | Default | |
|---|---|---|
| `FIREFLY_URL` | `http://app:8080` | URL interno di Firefly sulla rete Docker |
| `FIREFLY_TOKEN` | — | Personal Access Token (Firefly → Opzioni → Profilo → OAuth) |
| `FIREFLY_APPLY_RULES` | `true` | Fa applicare a Firefly anche le sue regole |
| `TELEGRAM_BOT_TOKEN` | — | Da @BotFather; se vuoto il bot è disattivato |
| `TELEGRAM_ALLOWED_CHAT_IDS` | — | Chat id autorizzati, separati da virgola (il bot ti dice il tuo con /start) |
| `PUBLIC_URL` | — | URL della web app, per i link nei messaggi del bot |
| `DATA_DIR` | `/data` | SQLite + file caricati |

## Deploy nello stack `firefly`

1. Pubblica l'immagine: push su GitHub (`faccus/firefly-ingest`). La workflow `.github/workflows/docker.yml` esegue i test
   e pubblica `ghcr.io/faccus/firefly-ingest`.
2. Aggiungi il servizio di [`deploy/compose.snippet.yml`](deploy/compose.snippet.yml) a `stacks/firefly/docker-compose.yml`
   e le variabili `FIREFLY_INGEST_*` a `stack.env` in Portainer.
3. In Nginx Proxy Manager crea un proxy host (es. `ingest.dymistahome.xyz`, solo LAN) verso `<ip-host>:42172`,
   come già fai per Firefly su 42170. Abilita SSL e, se vuoi, l'access list LAN.

L'app non ha un login proprio: è pensata per la LAN, dietro il reverse proxy. Il bot accetta solo i chat id autorizzati.

## Sviluppo

```bash
cd backend && uv run pytest -q          # test (Firefly simulato con respx)
cd backend && uv run uvicorn app.main:app --reload --port 8080
cd frontend && npm install && npm run dev   # http://localhost:5173, proxy /api -> :8080
```
