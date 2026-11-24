"""Tests for mermaid_parser.py. Run with:  python -m pytest test_parser.py  (or: python test_parser.py)"""
import glob
import os
from mermaid_parser import parse, parse_file, EXACTLY_ONE, ZERO_OR_ONE, ZERO_OR_MANY, ONE_OR_MANY


HERE = os.path.dirname(os.path.abspath(__file__))
DIAGRAM_DIR = os.path.join(HERE, "..", "diagrams")

# Kept inline so this test doesn't depend on any particular file in diagrams/.
ORDER_SCHEMA = """erDiagram
    WAREHOUSE {
        int warehouse_id PK
        string facility_code UK
    }
    ORDER {
        int order_id PK
        string reference_code UK
    }
    SHIPMENT {
        int shipment_id PK
        int order_id FK, UK
        string tracking_code UK
        int warehouse_id FK
    }
    ORDER ||--|| SHIPMENT : fulfilled_by
    WAREHOUSE ||--o{ SHIPMENT : dispatched_from
"""


def test_all_symbol_markers():
    s = parse("""erDiagram
    A |o--o| B : r1
    A ||--|| C : r2
    A }o--o{ D : r3
    A }|..|{ E : r4
    """)
    cards = [(r.left_card, r.right_card) for r in s.relationships]
    assert cards == [(ZERO_OR_ONE, ZERO_OR_ONE), (EXACTLY_ONE, EXACTLY_ONE),
                     (ZERO_OR_MANY, ZERO_OR_MANY), (ONE_OR_MANY, ONE_OR_MANY)]
    assert [r.kind for r in s.relationships] == ["1:1", "1:1", "M:N", "M:N"]
    assert s.relationships[3].identifying is False
    assert not s.errors


def test_direction_of_one_to_many():
    s = parse("erDiagram\n  ORDER }o--|| CUSTOMER : placed_by\n")
    r = s.relationships[0]
    assert r.kind == "N:1"
    assert r.parent_child() == ("CUSTOMER", "ORDER")


def test_word_syntax():
    s = parse('erDiagram\n  CAR 1 to zero or more NAMED-DRIVER : allows\n'
              '  A one or zero optionally to many(1) B : x\n')
    r1, r2 = s.relationships
    assert (r1.left, r1.right, r1.kind) == ("CAR", "NAMED-DRIVER", "1:N")
    assert r2.identifying is False and r2.right_card == ONE_OR_MANY


def test_attribute_keys_comments_and_types():
    s = parse('''erDiagram
    CUSTOMER {
        varchar(255) email UK "login name"
        int id PK
        int region_id FK, UK
    }''')
    attrs = s.entities["CUSTOMER"].attributes
    assert attrs["email"].keys == ["UK"] and attrs["email"].comment == "login name"
    assert attrs["email"].type == "varchar(255)"
    assert attrs["region_id"].keys == ["FK", "UK"]
    assert attrs["id"].is_unique          # PK implies unique
    assert not s.warnings or all("references" in w.message or "determine" in w.message
                                 for w in s.warnings)


def test_space_separated_keys_warn_but_parse():
    s = parse("erDiagram\n A {\n  int x FK UK\n }\n")
    assert s.entities["A"].attributes["x"].keys == ["FK", "UK"]
    assert any("comma-separated" in w.message for w in s.warnings)


def test_bad_input_is_reported_not_dropped():
    s = parse("""erDiagram
    A {
        int id PK
        string name XK
    }
    B o{--|| A : wrong_side
    nonsense here !!
    """)
    msgs = " ".join(d.message for d in s.errors)
    assert "unknown key 'XK'" in msgs
    assert "unrecognized relationship syntax" in msgs
    assert "unrecognized line" in msgs


def test_unclosed_block_and_missing_header():
    s = parse("A {\n int id PK\n")
    msgs = " ".join(d.message for d in s.errors)
    assert "erDiagram" in msgs and "never closed" in msgs


def test_aliases_quotes_frontmatter_and_bare_entities():
    s = parse('''---
title: demo
---
erDiagram
    %% a comment
    direction LR
    CUST["Customer Record"] {
        int id PK
    }
    LONELY
    "Order Line" }o--|| CUST : belongs_to
''')
    assert s.entities["CUST"].alias == "Customer Record"
    assert "LONELY" in s.entities
    assert "Order Line" in s.entities and not s.entities["Order Line"].declared
    assert not s.errors


def test_fk_resolution_and_foreign_keys_for():
    s = parse(ORDER_SCHEMA)
    ship = s.entities["SHIPMENT"].attributes
    assert ship["order_id"].references == "ORDER"
    assert ship["warehouse_id"].references == "WAREHOUSE"
    rel = next(r for r in s.relationships if r.label == "fulfilled_by")
    fks = s.foreign_keys_for(rel)
    assert [(e, a.name) for e, a in fks] == [("SHIPMENT", "order_id")]   # not warehouse_id


def test_fk_hint_and_missing_relationship():
    s = parse('''erDiagram
    EMPLOYEE {
        int employee_id PK
        int manager_id FK "references EMPLOYEE"
        int dept_id FK
    }
    DEPT {
        int dept_id PK
    }
    EMPLOYEE }o--o| EMPLOYEE : reports_to
''')
    attrs = s.entities["EMPLOYEE"].attributes
    assert attrs["manager_id"].references == "EMPLOYEE"
    assert attrs["dept_id"].references == "DEPT"
    assert any("no relationship line" in w.message for w in s.warnings)


def test_round_trip_all_diagrams():
    paths = sorted(glob.glob(os.path.join(DIAGRAM_DIR, "*.mmd")))
    assert paths, f"no .mmd files found in {DIAGRAM_DIR}"
    for path in paths:
        a = parse_file(path)
        b = parse(a.to_mermaid())
        strip = lambda d: [{k: v for k, v in x.items() if k != "line"} for x in d]
        assert strip(a.to_dict()["relationships"]) == strip(b.to_dict()["relationships"]), path
        for name, ent in a.entities.items():
            got = b.entities[name].attributes
            assert [(x.name, x.type, x.keys, x.references) for x in ent.attributes.values()] == \
                   [(x.name, x.type, x.keys, x.references) for x in got.values()], path
        assert not b.errors, path


def test_fk_matches_uk_column():
    s = parse("""erDiagram
    COUNTRY {
        int country_id PK
        string country_code UK
        string name
    }
    ADDRESS {
        int address_id PK
        string country_code FK
    }
    COUNTRY ||--o{ ADDRESS : located_in
""")
    fk = s.entities["ADDRESS"].attributes["country_code"]
    assert fk.references == "COUNTRY"
    assert fk.references_column == "country_code"
    rel = s.relationships[0]
    assert [(e, a.name) for e, a in s.foreign_keys_for(rel)] == [("ADDRESS", "country_code")]
    assert not s.warnings


def test_pk_match_beats_uk_match():
    # A connected table's PK is preferred over another connected table's UK.
    s = parse("""erDiagram
    A {
        int code PK
    }
    B {
        int b_id PK
        int code UK
    }
    C {
        int c_id PK
        int code FK
    }
    A ||--o{ C : r1
    B ||--o{ C : r2
""")
    assert s.entities["C"].attributes["code"].references == "A"


def test_fk_does_not_match_plain_column():
    # A same-named column WITHOUT a UK tag is not a valid FK target.
    s = parse("""erDiagram
    CATEGORY {
        int category_id PK
        string category_name
    }
    PRODUCT {
        int product_id PK
        string category_name FK
    }
    CATEGORY ||--o{ PRODUCT : groups
""")
    assert s.entities["PRODUCT"].attributes["category_name"].references is None
    assert any("could not determine" in w.message for w in s.warnings)


def test_references_column_for_pk_and_name_matches():
    s = parse("""erDiagram
    CUSTOMER {
        int id PK
    }
    ORDER {
        int order_id PK
        int customer_id FK
    }
    SHIPMENT {
        int shipment_id PK
        int order_id FK, UK
    }
    CUSTOMER ||--o{ ORDER : places
    ORDER ||--|| SHIPMENT : fulfilled_by
""")
    cust = s.entities["ORDER"].attributes["customer_id"]
    assert (cust.references, cust.references_column) == ("CUSTOMER", "id")      # name match
    order = s.entities["SHIPMENT"].attributes["order_id"]
    assert (order.references, order.references_column) == ("ORDER", "order_id")  # PK match


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
