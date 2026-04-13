# Far West Legacy — Obituary Data Schema

The extraction pipeline returns a single JSON object with the following structure.

---

## Top-level Fields

| Field | Type | Description |
|---|---|---|
| `deceased` | object | All data about the person who died |
| `relationships` | object | Named family members, grouped by relationship type |
| `eulogy_text` | string | Narrative/biographical portion of the obituary |
| `service_details` | string | Funeral/memorial service logistics and donation info |
| `source_url` | string | URL of the source obituary (empty string if pasted text) |
| `raw_text` | string | Full original obituary text, unmodified |

---

## `deceased` Object

| Field | Type | Description | Example |
|---|---|---|---|
| `given_names` | string | All given/first/middle names | `"Donna Sue"` |
| `surname` | string | Family surname at time of death | `"Neese"` |
| `maiden_name` | string | Birth surname (women only, from parenthetical) | `"Walker"` |
| `suffix` | string | Name suffix (Jr., Sr., III, etc.) | `"Jr."` or `""` |
| `gender` | string | Inferred from pronouns or name | `"Male"`, `"Female"`, `"Unknown"` |
| `birth_date` | string | ISO date or partial; empty if unknown | `"1939-07-25"`, `"1939-07"`, `"1939"` |
| `birth_place` | string | "City, State" or "City, County, State" | `"Jamesport, Missouri"` |
| `death_date` | string | ISO date or partial; empty if unknown | `"2025-12-10"` |
| `death_place` | string | Location of death | `"Pleasant Valley, Missouri"` |
| `burial_place` | string | Cemetery or location of interment | `"Jamesport Cemetery"` |

---

## `relationships` Object

Each relationship type is an array of person objects. Arrays are empty `[]` if no individuals are named.

### Common Person Object Fields

| Field | Type | Description |
|---|---|---|
| `given_names` | string | All given names |
| `surname` | string | Family surname |
| `maiden_name` | string | Birth surname if known (parents, siblings) |
| `deceased` | boolean | `true` if this person preceded the subject in death |

### `spouses`
Array of spouse objects. Includes `given_names`, `surname`. Add `"deceased": true` if preceded in death.

```json
"spouses": [
  {"given_names": "Robert", "surname": "Smith"},
  {"given_names": "Harold", "surname": "Jones", "deceased": true}
]
```

### `parents`
Array of parent objects. Includes `given_names`, `surname`, `maiden_name`. Both parents listed when named.

```json
"parents": [
  {"given_names": "Andrew", "surname": "Neese", "maiden_name": ""},
  {"given_names": "Nellie", "surname": "Neese", "maiden_name": "Walker"}
]
```

### `children`
Array of child objects. Includes `given_names`, `surname`. Add `"deceased": true` if preceded in death.

```json
"children": [
  {"given_names": "Mary Ellen", "surname": "Thompson"},
  {"given_names": "James", "surname": "Neese", "deceased": true}
]
```

### `siblings`
Array of sibling objects. Includes `given_names`, `surname`, `maiden_name`. Add `"deceased": true` if preceded in death.

```json
"siblings": [
  {"given_names": "Mavis", "surname": "Neese", "maiden_name": "", "deceased": true},
  {"given_names": "Betty", "surname": "Crawford", "maiden_name": "Neese"}
]
```

---

## Date Formats

| Known information | Format | Example |
|---|---|---|
| Full date | `YYYY-MM-DD` | `"1939-07-25"` |
| Month and year only | `YYYY-MM` | `"1939-07"` |
| Year only | `YYYY` | `"1939"` |
| Unknown | empty string | `""` |

---

## Full Example

```json
{
  "deceased": {
    "given_names": "Donna Sue",
    "surname": "Neese",
    "maiden_name": "",
    "suffix": "",
    "gender": "Female",
    "birth_date": "1939-07-25",
    "birth_place": "Jamesport, Missouri",
    "death_date": "2025-12-10",
    "death_place": "Pleasant Valley, Missouri",
    "burial_place": ""
  },
  "relationships": {
    "spouses": [],
    "parents": [
      {"given_names": "Andrew", "surname": "Neese", "maiden_name": ""},
      {"given_names": "Nellie", "surname": "Neese", "maiden_name": "Walker"}
    ],
    "children": [],
    "siblings": [
      {"given_names": "Mavis", "surname": "Neese", "maiden_name": "", "deceased": true},
      {"given_names": "Melvin", "surname": "Neese", "maiden_name": "", "deceased": true},
      {"given_names": "Marion", "surname": "Neese", "maiden_name": "", "deceased": true},
      {"given_names": "Allen", "surname": "Hunt", "maiden_name": "", "deceased": true}
    ]
  },
  "eulogy_text": "Donna was born July 25, 1939, in Jamesport, the daughter of Andrew and Nellie (Walker) Neese. She graduated from Jamesport High School and spent much of her life helping her brother, Mavis Neese, on the family farm. Donna was a hardworking, gentle soul. Timid by nature, yet deeply caring, she had a special love for children and animals and often took in strays who needed a safe place. Her quiet compassion and steadfast faith in Christ guided her life and touched those around her.",
  "service_details": "",
  "source_url": "",
  "raw_text": "Donna Sue Neese, age 86..."
}
```
