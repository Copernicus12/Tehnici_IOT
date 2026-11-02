# Laborator FastAPI

Am făcut o aplicație mică în Python care primește temperatura și umiditatea de la senzori folosind FastAPI. Fiecare senzor are un nume, iar fiecare măsurătoare are un număr de ordine.

Aplicația primește datele, verifică dacă sunt corecte și le ține în memorie. După aceea pot să cer citirile unui senzor și să aleg ce vreau să văd. Pentru laborator am trimis datele din terminal, fără să fie nevoie de un senzor fizic.

Codul aplicației este în [hello.py](./hello.py).

## Formatul citirii

De exemplu, trimit următoarele date:

```json
{
  "dev": "senzor-1",
  "seq": 1,
  "ts": 1762096000,
  "temp": 21.5,
  "hum": 40
}
```

- `dev` este numele senzorului. Acceptă litere mici, cifre și cratimă, între 1 și 32 de caractere.
- `seq` este numărul citirii și trebuie să fie cel puțin 0.
- `ts` este momentul măsurării, exprimat în secunde Unix. Trebuie să fie mai mare decât `1600000000`.
- `temp` este temperatura în grade Celsius, între -40 și 85.
- `hum` este umiditatea în procente, între 0 și 100. Poate să lipsească; atunci apare ca `null` în răspuns.

Modelul `Reading`, făcut cu Pydantic, verifică automat regulile de mai sus. Dacă trimit ceva greșit, cererea este respinsă cu codul `422`.


## Metoda de salvare a datelor

Folosesc `POST /readings` și trimit citirea ca JSON.

În cod există un dicționar numit `DB`. Primul nivel este numele senzorului, iar al doilea este numărul citirii. De exemplu, `DB["senzor-1"][1]` păstrează citirea cu numărul 1 de la `senzor-1`.

Dacă datele sunt valide și citirea este nouă, aplicația o salvează și o trimite înapoi cu codul `201`. Dacă trimit din nou același `seq` pentru același senzor, primesc `409`, fiindcă acea citire există deja. Citirea veche nu este suprascrisă.

Doi senzori diferiți pot avea fiecare o citire cu `seq=1`.

## Comanda de Request a datelor

Folosesc `GET /devices/senzor-1/readings` ca să văd citirile de la `senzor-1`.

Pot adăuga câțiva parametri:

- `since=1` îmi dă doar citirile cu `seq` mai mare decât 1.
- `limit=1` îmi dă cel mult o citire. Implicit este 10, iar limita acceptată este între 1 și 100.
- `min_temp=20` păstrează doar citirile cu temperatura de cel puțin 20°C. Acesta este unul dintre bonusuri.

De exemplu:

```text
GET /devices/senzor-1/readings?since=1&limit=10
```

Dacă am salvat citirile 1 și 2, cererea aceasta îmi întoarce doar citirea 2. Acesta este testul 7 din laborator.

Aplicația filtrează mai întâi citirile, apoi le ordonează crescător după `seq` și la final aplică limita. `since` se referă la numărul citirii, nu la timpul din `ts`.

Dacă senzorul nu are citiri salvate, primesc `404`. Dacă există, dar niciuna dintre citiri nu trece de filtre, primesc o listă goală, `[]`, cu codul `200`.


## Alte comenzi de verificare utile

`GET /health` răspunde cu `{"status": "ok"}`. Îl folosesc ca să verific dacă aplicația răspunde.

`GET /devices` îmi arată lista senzorilor și câte citiri are fiecare. Și acesta este un bonus.

Codurile de răspuns folosite sunt:

| Cod | Ce înseamnă aici |
| --- | --- |
| `200` | Cererea GET a mers. |
| `201` | Citirea a fost salvată. |
| `404` | Senzorul nu are citiri salvate. |
| `409` | Citirea există deja. |
| `422` | Datele sau parametrii trimiși nu respectă regulile. |

La testele cu date greșite, răspunsurile `404`, `409` și `422` sunt cele așteptate.