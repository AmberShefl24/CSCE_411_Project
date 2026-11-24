# Mermaid ER Diagram Parser

CSCE 411 Final Project, Fall 2026 — Defense Line

`mermaid_parser.py` reads a Mermaid `erDiagram` file and turns it into Python
objects: entities, attributes with their key tags (PK, FK, UK), relationships
with their cardinalities, and the table and column each foreign key points to.
The uniqueness validator works from this output instead of raw text.

Every line the parser cannot understand is reported with its line number.
Nothing is skipped silently.

## Requirements

- Python 3.8 or newer (tested on 3.13)
- No third-party packages

On macOS, use `python3` instead of `python`.

## Files

| File | Purpose |
| --- | --- |
| `mermaid_parser.py` | The parser (library and command-line tool) |
| `test_parser.py` | 15 tests for the parser |
| `diagrams/*.mmd` | Toy input diagrams |

## Quick start

```
python3 mermaid_parser.py diagrams/bookstore.mmd
python3 test_parser.py
```

The test run should print 15 lines starting with `ok`.

## Input format

A text file containing a Mermaid ER diagram:

```
erDiagram
    AUTHOR {
        int author_id PK
        string name
        string email UK
    }
    BOOK {
        int book_id PK
        string title
        string isbn UK
        int author_id FK
    }
    AUTHOR ||--o{ BOOK : writes
```

**Attributes** are written `type name [keys] ["comment"]`.

- Keys are `PK`, `FK` and `UK`. Separate several with commas: `int order_id FK, UK`.
  Spaces also parse, but give a warning because Mermaid may not render them.
- A comment can tell the parser which table an FK points to when the name
  doesn't make it obvious: `int manager_id FK "references EMPLOYEE"`.

**Relationships** are written `LEFT <marker><line><marker> RIGHT : label`.

| Meaning | Left marker | Right marker |
| --- | --- | --- |
| Zero or one | `\|o` | `o\|` |
| Exactly one | `\|\|` | `\|\|` |
| Zero or many | `}o` | `o{` |
| One or many | `}\|` | `\|{` |

The line is `--` (solid, identifying) or `..` (dashed, non-identifying).
The word form also works: `CAR 1 to zero or more DRIVER : allows`.

Also accepted: `%%` comments, YAML front matter, `direction`, entity aliases
(`CUST["Customer"]`), quoted and hyphenated names, and entities that appear
only in a relationship.

## Command-line usage

```
python3 mermaid_parser.py FILE [--json | --emit] [--strict]
```

| Option | Effect |
| --- | --- |
| *(none)* | Print a readable summary |
| `--json` | Print the parsed schema as JSON |
| `--emit` | Print the schema re-written as Mermaid |
| `--strict` | Exit with status 1 if there are any parse errors |

**Exit codes:** `0` success, `1` parse errors with `--strict`, `2` file not readable.

### Example output

`python3 mermaid_parser.py diagrams/bookstore.mmd`

```
Entities: 2   Relationships: 1

  AUTHOR
    int        author_id                PK
    string     name
    string     email                    UK

  BOOK
    int        book_id                  PK
    string     title
    string     isbn                     UK
    int        author_id                FK        -> AUTHOR.author_id

  AUTHOR [1..1] --1:N-- [0..N] BOOK  : writes  (solid, line 13)
```

`-> AUTHOR.author_id` shows the table and column the FK was matched to.
`[1..1]` and `[0..N]` are the minimum and maximum on each side.

A diagram with mistakes:

```
erDiagram
 A {
  int id PK
  string x XK
 }
 B o{--|| A : bad
```

```
  [ERROR] line 4: entity 'A', attribute 'x': unknown key 'XK' (allowed: PK, FK, UK)
  [ERROR] line 6: unrecognized relationship syntax: 'B o{--|| A : bad'. Left-side
          markers are |o || }o }| and right-side markers are o| || o{ |{
```

### JSON output

`--json` prints:

```
{
  "entities": [
    {
      "name": "BOOK", "alias": null, "line": 7, "declared": true,
      "attributes": [
        {"name": "author_id", "type": "int", "keys": ["FK"], "comment": null,
         "references": "AUTHOR", "references_column": "author_id", "line": 11}
      ]
    }
  ],
  "relationships": [
    {"left": "AUTHOR", "right": "BOOK", "left_card": "1..1", "right_card": "0..N",
     "kind": "1:N", "identifying": true, "label": "writes", "line": 13}
  ],
  "diagnostics": [
    {"line": 4, "severity": "error", "message": "..."}
  ]
}
```

(Shortened; the real output lists every entity and attribute.)

## Using it from Python

```python
from mermaid_parser import parse_file, parse

schema = parse_file("diagrams/bookstore.mmd")   # or parse(text)

for rel in schema.relationships:
    print(rel.left, rel.kind, rel.right)                # AUTHOR 1:N BOOK
    for table, fk in schema.foreign_keys_for(rel):
        print(table, fk.name, fk.keys, fk.references_column)

for problem in schema.errors:      # also schema.warnings, schema.diagnostics
    print(problem)
```

| Object | Useful fields and methods |
| --- | --- |
| `Schema` | `entities` (dict), `relationships`, `diagnostics`, `errors`, `warnings`, `foreign_keys_for(rel)`, `to_dict()`, `to_mermaid()` |
| `Entity` | `name`, `alias`, `attributes` (dict), `primary_keys()`, `foreign_keys()` |
| `Attribute` | `name`, `type`, `keys`, `comment`, `references`, `references_column`, `is_pk`, `is_fk`, `is_uk`, `is_unique` (PK or UK) |
| `Relationship` | `left`, `right`, `left_card`, `right_card`, `identifying`, `label`, `kind` (`1:1`, `1:N`, `N:1`, `M:N`), `parent_child()` |
| `Cardinality` | `min`, `max` (`None` means many), `is_many`, `is_optional` |

`foreign_keys_for(rel)` returns only the FKs that implement that relationship.
Use it instead of checking every FK in a table, or an unrelated FK (for example
`SHIPMENT.warehouse_id` for the `ORDER ||--|| SHIPMENT` relationship) gets
checked against the wrong rule.

## How foreign keys are matched

Mermaid has no syntax for which table an FK references, so the parser infers
it. It tries these rules in order and uses the first that gives exactly one
match:

1. A comment hint: `"references X"`, `"ref X"` or `"-> X"`
2. A connected table whose **PK** has the same name as the FK
3. A connected table whose **UK** has the same name as the FK
4. A connected table named like the FK without `_id` (`customer_id` → `CUSTOMER`)
5. Rules 2–4 against tables with no relationship line (gives a warning that
   the relationship line is missing)

"Connected" means joined by a relationship line. If two tables match, the
parser warns that the FK is ambiguous. If nothing matches, it warns that the
target could not be determined. In both cases it does not guess.

## Known limitations

- Unusual FK names need a `"references ..."` comment.
- UK matching needs an exact name (`holder_ssn` does not match `ssn`).
- FK matching works on single columns, not composite keys.
- Attributes must start on the line after `{`.