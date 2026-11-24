# Mermaid ER Diagram Uniqueness Validator

CSCE 411 Final Project, Fall 2026 — Defense Line

**Team:** *Amber Shefl, Ryan Bussom, Bryce Closman*

## Overview

This project automatically checks Mermaid ER diagrams (`erDiagram`) for design
errors in uniqueness and key constraints. It targets errors that aren't simple
syntax mistakes: a diagram can render perfectly and still describe a database
that can't enforce the relationships it shows.

For example, a diagram may draw a one-to-many relationship between `PATIENT`
and `APPOINTMENT`, but if `APPOINTMENT.patient_id` is tagged `UK`, the database
only allows one appointment per patient. The diagram and its keys disagree,
and the validator reports it.

## Status

| Part | Status |
| --- | --- |
| Diagram parser (`er_parser/`) | Done, 15 tests passing |
| User uniqueness rules (input format and the code that reads it) | Not started |
| Validator (rules 1–5) | Not started |

## Validation rules

The validator will check two kinds of rules.

**Structural rules** are built in. They follow from how keys and
relationships work, so they apply to every diagram:

| # | Rule | Example of a violation |
| --- | --- | --- |
| 1 | A PK is already unique; a UK tag beside it is redundant (warning) | `int id PK, UK` |
| 2 | In a 1:1 relationship, the FK must be UK or PK | `ORDER \|\|--\|\| PAYMENT` with `PAYMENT.order_id FK` |
| 3 | In a 1:M relationship, the FK on the many side must not be UK | `PATIENT \|\|--o{ APPOINTMENT` with `APPOINTMENT.patient_id FK, UK` |

**Uniqueness rules** will come from the user. Which columns should be unique
depends on the business domain (an email is unique in one system and shared
in another), so the validator won't guess from column names. The user will
list the columns that must be unique, and every other column is treated as
not unique:

| # | Rule | Example of a violation |
| --- | --- | --- |
| 4 | Every listed attribute carries a UK (or PK) tag | `CUSTOMER.ssn` is listed; diagram has `string ssn` |
| 5 | Every attribute not listed carries no UK tag | `CUSTOMER.full_name` isn't listed; diagram has `string full_name UK` |

Rules 4 and 5 skip two kinds of columns:

- **PK columns** are always unique, so they never need to be listed.
- **FK columns** are covered by rules 2 and 3, since whether an FK should be
  unique depends on its relationship, not on the user's list.

## Project structure

```
CSCE_411_Project/
├── README.md            this file
├── .gitignore
├── diagrams/            toy input diagrams (.mmd)
├── er_parser/           parser: reads .mmd files into Python objects
│   ├── README.md        parser documentation
│   ├── mermaid_parser.py
│   └── test_parser.py
└── validator/           validator: applies the rules above (not started)
```

## Requirements

- Python 3.8 or newer (tested on 3.13)
- No third-party packages

On macOS, use `python3` instead of `python`. Run all commands from the
`CSCE_411_Project` folder.

## Quick start

Parse a diagram and see its tables, keys and relationships:

```
python3 er_parser/mermaid_parser.py diagrams/bookstore.mmd
```

Run the parser tests (expect 15 lines starting with `ok`):

```
python3 er_parser/test_parser.py
```

The validator command will go here once it's built.

## How it works

1. **Parse the diagram.** `er_parser/` reads the `.mmd` file into tables,
   columns, key tags and relationships, and works out which table each FK
   points to. See [`er_parser/README.md`](er_parser/README.md) for details.
2. **Read the user's uniqueness rules**
