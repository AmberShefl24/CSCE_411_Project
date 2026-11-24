#!/usr/bin/env python3
"""
mermaid_parser.py - Parser for Mermaid erDiagram source files.

Turns .mmd text into a Schema object (entities, attributes, key tags,
relationships with cardinalities, and resolved foreign-key targets) that a
validator can inspect. It can also write a Schema back out as Mermaid, so an
offense-line generator can parse a diagram, mutate it, and save the result.

Nothing is dropped silently: every line the parser cannot understand becomes a
Diagnostic with a line number.

Usage:
    python mermaid_parser.py diagram.mmd            # readable summary
    python mermaid_parser.py diagram.mmd --json     # JSON dump of the schema
    python mermaid_parser.py diagram.mmd --emit     # re-serialize to Mermaid
    python mermaid_parser.py diagram.mmd --strict   # exit 1 on any parse error

Library use:
    from mermaid_parser import parse_file
    schema = parse_file("diagrams/hospital.mmd")
    for rel in schema.relationships:
        print(rel.kind, schema.foreign_keys_for(rel))
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

VALID_KEYS = ("PK", "FK", "UK")


# --------------------------------------------------------------------------
# Cardinality
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Cardinality:
    min: int                 # 0 or 1
    max: Optional[int]       # 1, or None for "many"

    @property
    def is_many(self) -> bool:
        return self.max is None

    @property
    def is_optional(self) -> bool:
        return self.min == 0

    def __str__(self) -> str:
        return f"{self.min}..{'N' if self.max is None else self.max}"


ZERO_OR_ONE = Cardinality(0, 1)
EXACTLY_ONE = Cardinality(1, 1)
ZERO_OR_MANY = Cardinality(0, None)
ONE_OR_MANY = Cardinality(1, None)

# Mermaid uses different symbols on each side of the line.
LEFT_SYMBOLS = {"|o": ZERO_OR_ONE, "||": EXACTLY_ONE, "}o": ZERO_OR_MANY, "}|": ONE_OR_MANY}
RIGHT_SYMBOLS = {"o|": ZERO_OR_ONE, "||": EXACTLY_ONE, "o{": ZERO_OR_MANY, "|{": ONE_OR_MANY}
LEFT_OUT = {v: k for k, v in LEFT_SYMBOLS.items()}
RIGHT_OUT = {v: k for k, v in RIGHT_SYMBOLS.items()}

# Mermaid's word-based alternative syntax, e.g. "CAR 1 to zero or more DRIVER : allows"
WORD_ALIASES = {
    "one or zero": ZERO_OR_ONE, "zero or one": ZERO_OR_ONE,
    "only one": EXACTLY_ONE, "1": EXACTLY_ONE,
    "zero or more": ZERO_OR_MANY, "zero or many": ZERO_OR_MANY,
    "many(0)": ZERO_OR_MANY, "0+": ZERO_OR_MANY,
    "one or more": ONE_OR_MANY, "one or many": ONE_OR_MANY,
    "many(1)": ONE_OR_MANY, "1+": ONE_OR_MANY,
}


# --------------------------------------------------------------------------
# Schema model
# --------------------------------------------------------------------------
@dataclass
class Attribute:
    name: str
    type: str
    keys: List[str] = field(default_factory=list)
    comment: Optional[str] = None
    line: int = 0
    references: Optional[str] = None          # FK target entity, set by resolve_foreign_keys()
    references_column: Optional[str] = None   # column in that entity the FK matches, if known

    @property
    def is_pk(self) -> bool:
        return "PK" in self.keys

    @property
    def is_fk(self) -> bool:
        return "FK" in self.keys

    @property
    def is_uk(self) -> bool:
        return "UK" in self.keys

    @property
    def is_unique(self) -> bool:
        """PK implies uniqueness, so either tag makes the column unique."""
        return self.is_pk or self.is_uk

    def to_dict(self) -> dict:
        return {"name": self.name, "type": self.type, "keys": self.keys,
                "comment": self.comment, "references": self.references,
                "references_column": self.references_column, "line": self.line}


@dataclass
class Entity:
    name: str
    alias: Optional[str] = None
    attributes: Dict[str, Attribute] = field(default_factory=dict)
    line: int = 0
    declared: bool = True   # False if it only appears inside a relationship

    def primary_keys(self) -> List[Attribute]:
        return [a for a in self.attributes.values() if a.is_pk]

    def foreign_keys(self) -> List[Attribute]:
        return [a for a in self.attributes.values() if a.is_fk]

    def to_dict(self) -> dict:
        return {"name": self.name, "alias": self.alias, "line": self.line,
                "declared": self.declared,
                "attributes": [a.to_dict() for a in self.attributes.values()]}


@dataclass
class Relationship:
    left: str
    right: str
    left_card: Cardinality
    right_card: Cardinality
    identifying: bool        # True for solid "--", False for dashed ".."
    label: str = ""
    line: int = 0

    @property
    def kind(self) -> str:
        """'1:1', '1:N', 'N:1' or 'M:N', reading left to right.

        Note the marker next to an entity describes how many of THAT entity
        take part, so in A ||--o{ B the 'o{' means many B per A.
        """
        l_many, r_many = self.left_card.is_many, self.right_card.is_many
        if l_many and r_many:
            return "M:N"
        if r_many:
            return "1:N"
        if l_many:
            return "N:1"
        return "1:1"

    def parent_child(self) -> Optional[Tuple[str, str]]:
        """For a one-to-many relationship, return (parent, child), else None."""
        if self.kind == "1:N":
            return self.left, self.right
        if self.kind == "N:1":
            return self.right, self.left
        return None

    def to_dict(self) -> dict:
        return {"left": self.left, "right": self.right,
                "left_card": str(self.left_card), "right_card": str(self.right_card),
                "kind": self.kind, "identifying": self.identifying,
                "label": self.label, "line": self.line}


@dataclass
class Diagnostic:
    line: int
    severity: str            # "error" or "warning"
    message: str

    def __str__(self) -> str:
        where = f"line {self.line}" if self.line else "file"
        return f"[{self.severity.upper()}] {where}: {self.message}"


@dataclass
class Schema:
    entities: Dict[str, Entity] = field(default_factory=dict)
    relationships: List[Relationship] = field(default_factory=list)
    diagnostics: List[Diagnostic] = field(default_factory=list)

    @property
    def errors(self) -> List[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "error"]

    @property
    def warnings(self) -> List[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "warning"]

    def foreign_keys_for(self, rel: Relationship) -> List[Tuple[str, Attribute]]:
        """FK attributes that implement this relationship, as (entity, attribute).

        Validators should use this instead of looking at every FK in an
        entity, otherwise an unrelated FK (e.g. SHIPMENT.warehouse_id) gets
        blamed for the ORDER ||--|| SHIPMENT relationship.
        """
        found = []
        for owner, target in ((rel.left, rel.right), (rel.right, rel.left)):
            ent = self.entities.get(owner)
            if ent is None:
                continue
            for attr in ent.foreign_keys():
                if attr.references == target:
                    found.append((owner, attr))
            if owner == target:   # self-relationship: don't count twice
                break
        return found

    def to_dict(self) -> dict:
        return {"entities": [e.to_dict() for e in self.entities.values()],
                "relationships": [r.to_dict() for r in self.relationships],
                "diagnostics": [vars(d) for d in self.diagnostics]}

    def to_mermaid(self) -> str:
        """Serialize back to Mermaid erDiagram source."""
        out = ["erDiagram"]
        in_rels = {r.left for r in self.relationships} | {r.right for r in self.relationships}
        for ent in self.entities.values():
            head = _quote_name(ent.name) + (f'["{ent.alias}"]' if ent.alias else "")
            if ent.attributes:
                out.append(f"    {head} {{")
                for a in ent.attributes.values():
                    parts = [a.type, a.name]
                    if a.keys:
                        parts.append(", ".join(a.keys))
                    if a.comment is not None:
                        parts.append(f'"{a.comment}"')
                    out.append("        " + " ".join(parts))
                out.append("    }")
            elif ent.alias or ent.name not in in_rels:
                out.append(f"    {head}")
        for r in self.relationships:
            line = "--" if r.identifying else ".."
            label = r.label if re.fullmatch(r"[\w-]+", r.label or "") else f'"{r.label}"'
            out.append(f"    {_quote_name(r.left)} {LEFT_OUT[r.left_card]}{line}"
                       f"{RIGHT_OUT[r.right_card]} {_quote_name(r.right)} : {label}")
        return "\n".join(out) + "\n"


def _quote_name(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z_][\w-]*", name) else f'"{name}"'


# --------------------------------------------------------------------------
# Line patterns
# --------------------------------------------------------------------------
NAME = r'(?:"[^"]+"|[A-Za-z_][\w-]*)'
ALIAS = r'(?:\[\s*"?(?P<alias>[^"\]]*)"?\s*\])?'

ENTITY_OPEN_RE = re.compile(rf'^(?P<name>{NAME})\s*{ALIAS}\s*\{{\s*(?P<rest>.*)$')
ENTITY_BARE_RE = re.compile(rf'^(?P<name>{NAME})\s*{ALIAS}$')

_LSYM = "|".join(re.escape(s) for s in LEFT_SYMBOLS)
_RSYM = "|".join(re.escape(s) for s in RIGHT_SYMBOLS)
_LABEL = r'(?:\s*:\s*(?P<label>.*))?'
SYMBOL_REL_RE = re.compile(
    rf'^(?P<left>{NAME})\s*(?P<lc>{_LSYM})(?P<line>--|\.\.)(?P<rc>{_RSYM})\s*(?P<right>{NAME}){_LABEL}$')

_WORDS = "|".join(re.escape(w) for w in sorted(WORD_ALIASES, key=len, reverse=True))
WORD_REL_RE = re.compile(
    rf'^(?P<left>{NAME})\s+(?P<lc>{_WORDS})\s+(?P<line>optionally to|to)\s+'
    rf'(?P<rc>{_WORDS})\s+(?P<right>{NAME}){_LABEL}$', re.IGNORECASE)

# Anything that looks like it was meant to be a relationship line.
LOOKS_LIKE_REL_RE = re.compile(r'[|}{o]{1,2}\s*(--|\.\.)\s*[|}{o]{1,2}')

ATTR_RE = re.compile(r'^(?P<type>[^\s"]+)\s+(?P<name>[^\s",]+)(?P<rest>.*)$')
COMMENT_RE = re.compile(r'"(?P<comment>[^"]*)"\s*$')
FK_HINT_RE = re.compile(r'(?:references|refs?|->)\s*:?\s*"?(?P<target>[A-Za-z_][\w-]*)', re.IGNORECASE)

SKIP_PREFIXES = ("direction ", "style ", "classDef ", "class ", "accTitle", "accDescr", "title ")


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------
class _Parser:
    def __init__(self) -> None:
        self.schema = Schema()

    def diag(self, line: int, severity: str, msg: str) -> None:
        self.schema.diagnostics.append(Diagnostic(line, severity, msg))

    def get_entity(self, raw_name: str, line: int, declared: bool) -> Entity:
        name = raw_name.strip('"')
        ent = self.schema.entities.get(name)
        if ent is None:
            ent = Entity(name=name, line=line, declared=declared)
            self.schema.entities[name] = ent
        elif declared and not ent.declared:
            ent.declared, ent.line = True, line
        return ent

    def parse(self, text: str) -> Schema:
        lines = text.splitlines()
        start = 0
        if lines and lines[0].strip() == "---":            # YAML front matter
            for j in range(1, len(lines)):
                if lines[j].strip() == "---":
                    start = j + 1
                    break

        seen_header = False
        current: Optional[Entity] = None

        for idx in range(start, len(lines)):
            n = idx + 1
            line = lines[idx].strip()
            if not line or line.startswith("%%"):
                continue

            if not seen_header:
                if line.split()[0] == "erDiagram":
                    seen_header = True
                    continue
                self.diag(n, "error", "expected 'erDiagram' header before any content")
                seen_header = True   # report once, keep going

            if current is not None:
                if line == "}":
                    current = None
                elif ENTITY_OPEN_RE.match(line):
                    self.diag(current.line, "error",
                              f"entity block '{current.name}' is never closed with '}}'")
                    current = self.open_entity(line, n)
                else:
                    self.parse_attribute(current, line, n)
                continue

            if line.startswith(SKIP_PREFIXES):
                continue
            if line == "}":
                self.diag(n, "error", "'}' without a matching entity block")
                continue
            if ENTITY_OPEN_RE.match(line):
                current = self.open_entity(line, n)
                continue
            if self.parse_relationship(line, n):
                continue
            m = ENTITY_BARE_RE.match(line)
            if m:
                ent = self.get_entity(m.group("name"), n, declared=True)
                ent.alias = m.group("alias") or ent.alias
                continue
            if LOOKS_LIKE_REL_RE.search(line):
                self.diag(n, "error",
                          f"unrecognized relationship syntax: '{line}'. Left-side markers are "
                          f"|o || }}o }}| and right-side markers are o| || o{{ |{{")
            else:
                self.diag(n, "error", f"unrecognized line: '{line}'")

        if current is not None:
            self.diag(current.line, "error",
                      f"entity block '{current.name}' is never closed with '}}'")
        if not seen_header:
            self.diag(0, "error", "empty diagram: no 'erDiagram' header found")

        resolve_foreign_keys(self.schema)
        return self.schema

    def open_entity(self, line: str, n: int) -> Optional[Entity]:
        m = ENTITY_OPEN_RE.match(line)
        ent = self.get_entity(m.group("name"), n, declared=True)
        if m.group("alias"):
            ent.alias = m.group("alias")
        rest = m.group("rest").strip()
        if rest == "}":                      # empty block: NAME { }
            return None
        if rest:
            self.diag(n, "error", "attributes must start on the line after '{'")
        return ent

    def parse_attribute(self, ent: Entity, line: str, n: int) -> None:
        m = ATTR_RE.match(line)
        if not m:
            self.diag(n, "error", f"entity '{ent.name}': cannot parse attribute '{line}' "
                                  f"(expected: type name [PK|FK|UK] [\"comment\"])")
            return
        rest = m.group("rest").strip()
        comment = None
        cm = COMMENT_RE.search(rest)
        if cm:
            comment = cm.group("comment")
            rest = rest[:cm.start()].strip()

        keys: List[str] = []
        for tok in filter(None, re.split(r"[,\s]+", rest)):
            key = tok.upper()
            if key not in VALID_KEYS:
                self.diag(n, "error", f"entity '{ent.name}', attribute '{m.group('name')}': "
                                      f"unknown key '{tok}' (allowed: PK, FK, UK)")
            elif key in keys:
                self.diag(n, "warning", f"entity '{ent.name}', attribute '{m.group('name')}': "
                                        f"key '{key}' listed twice")
            else:
                keys.append(key)
        if len(keys) > 1 and re.search(r"\b(PK|FK|UK)\s+(PK|FK|UK)\b", rest, re.IGNORECASE):
            self.diag(n, "warning", f"entity '{ent.name}', attribute '{m.group('name')}': "
                                    f"Mermaid documents multiple keys as comma-separated "
                                    f"(e.g. 'FK, UK'); '{rest}' may not render")

        name = m.group("name")
        if name in ent.attributes:
            self.diag(n, "warning", f"entity '{ent.name}': attribute '{name}' declared twice; "
                                    f"keeping the later one")
        ent.attributes[name] = Attribute(name=name, type=m.group("type"), keys=keys,
                                         comment=comment, line=n)

    def parse_relationship(self, line: str, n: int) -> bool:
        m = SYMBOL_REL_RE.match(line)
        if m:
            lc, rc = LEFT_SYMBOLS[m.group("lc")], RIGHT_SYMBOLS[m.group("rc")]
            identifying = m.group("line") == "--"
        else:
            m = WORD_REL_RE.match(line)
            if not m:
                return False
            lc, rc = WORD_ALIASES[m.group("lc").lower()], WORD_ALIASES[m.group("rc").lower()]
            identifying = m.group("line").lower() == "to"

        label = (m.group("label") or "").strip().strip('"')
        if not label:
            self.diag(n, "warning", "relationship has no label (Mermaid expects ': label')")
        left = self.get_entity(m.group("left"), n, declared=False)
        right = self.get_entity(m.group("right"), n, declared=False)
        self.schema.relationships.append(Relationship(
            left=left.name, right=right.name, left_card=lc, right_card=rc,
            identifying=identifying, label=label, line=n))
        return True


# --------------------------------------------------------------------------
# Foreign-key resolution
# --------------------------------------------------------------------------
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def resolve_foreign_keys(schema: Schema) -> None:
    """Work out which entity (and column) each FK attribute points to.

    Mermaid has no syntax for FK targets, so this uses, in order:
      1. an explicit hint in the comment: "references CUSTOMER" / "-> CUSTOMER"
      2. a connected entity whose PK has the same name (customer_id = customer_id)
      3. a connected entity whose UK has the same name (country_code = country_code UK)
      4. a connected entity named like the column (customer_id -> CUSTOMER)
      5. rules 2-4 against UNCONNECTED entities (reported: relationship line missing)
    A rule only applies when it gives exactly one candidate. Zero candidates moves
    on to the next rule; two or more stops with an "ambiguous" warning.
    """
    ents = schema.entities
    neighbors: Dict[str, set] = {name: set() for name in ents}
    for r in schema.relationships:
        neighbors[r.left].add(r.right)
        neighbors[r.right].add(r.left)

    # Each rule returns a list of (entity name, matched column name or None).
    def pk_match(cands, attr):
        return [(c, attr.name) for c in cands
                if any(pk.name == attr.name for pk in ents[c].primary_keys())]

    def uk_match(cands, attr):
        return [(c, attr.name) for c in cands
                if any(a.name == attr.name and a.is_uk for a in ents[c].attributes.values())]

    def name_match(cands, attr):
        stem = _norm(re.sub(r"_?id$", "", attr.name, flags=re.IGNORECASE))
        return [(c, _single_pk(ents[c])) for c in cands if stem and _norm(c) == stem]

    rules = (pk_match, uk_match, name_match)

    def first_match(cands, attr):
        """Run the rules in order. Returns (match or None, ambiguous list or None)."""
        for rule in rules:
            found = rule(cands, attr)
            if len(found) == 1:
                return found[0], None
            if len(found) > 1:
                return None, [c for c, _ in found]
        return None, None

    for ent in ents.values():
        for attr in ent.foreign_keys():
            where = f"entity '{ent.name}', FK '{attr.name}'"

            if attr.comment:
                hint = FK_HINT_RE.search(attr.comment)
                if hint:
                    target = next((e for e in ents if e.lower() == hint.group("target").lower()), None)
                    if target:
                        attr.references = target
                        col = ents[target].attributes.get(attr.name)
                        attr.references_column = (col.name if col and col.is_unique
                                                  else _single_pk(ents[target]))
                        if target not in neighbors[ent.name]:
                            schema.diagnostics.append(Diagnostic(attr.line, "warning",
                                f"{where} references '{target}' but no relationship line "
                                f"connects them"))
                        continue
                    schema.diagnostics.append(Diagnostic(attr.line, "warning",
                        f"{where}: comment names unknown entity '{hint.group('target')}'"))

            match, ambiguous = first_match(sorted(neighbors[ent.name]), attr)
            if ambiguous:
                schema.diagnostics.append(Diagnostic(attr.line, "warning",
                    f"{where} is ambiguous between {ambiguous}; add a comment like "
                    f"\"references {ambiguous[0]}\""))
                continue
            if match:
                attr.references, attr.references_column = match
                continue

            far = sorted(e for e in ents if e != ent.name and e not in neighbors[ent.name])
            match, _ = first_match(far, attr)
            if match:
                attr.references, attr.references_column = match
                schema.diagnostics.append(Diagnostic(attr.line, "warning",
                    f"{where} appears to reference '{match[0]}' but no relationship line "
                    f"connects them"))
            else:
                schema.diagnostics.append(Diagnostic(attr.line, "warning",
                    f"{where}: could not determine which entity it references"))


def _single_pk(ent: Entity) -> Optional[str]:
    """The entity's PK column name if it has exactly one, else None."""
    pks = ent.primary_keys()
    return pks[0].name if len(pks) == 1 else None


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def parse(text: str) -> Schema:
    """Parse Mermaid erDiagram source text into a Schema."""
    return _Parser().parse(text)


def parse_file(path: str) -> Schema:
    with open(path, "r", encoding="utf-8") as f:
        return parse(f.read())


def _summary(schema: Schema) -> str:
    out = [f"Entities: {len(schema.entities)}   Relationships: {len(schema.relationships)}"]
    for ent in schema.entities.values():
        out.append(f"\n  {ent.name}" + ("" if ent.declared else "  (from relationship only)"))
        for a in ent.attributes.values():
            ref = ""
            if a.references:
                ref = f"  -> {a.references}" + (f".{a.references_column}" if a.references_column else "")
            keys = ",".join(a.keys)
            out.append(f"    {a.type:<10} {a.name:<24} {keys:<8}{ref}")
    if schema.relationships:
        out.append("")
    for r in schema.relationships:
        style = "solid" if r.identifying else "dashed"
        out.append(f"  {r.left} [{r.left_card}] --{r.kind}-- [{r.right_card}] {r.right}"
                   f"  : {r.label}  ({style}, line {r.line})")
    if schema.diagnostics:
        out.append("")
        out.extend(f"  {d}" for d in schema.diagnostics)
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Parse a Mermaid erDiagram file.")
    ap.add_argument("file", help="path to a .mmd file")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--json", action="store_true", help="print the parsed schema as JSON")
    group.add_argument("--emit", action="store_true", help="print the schema re-serialized as Mermaid")
    ap.add_argument("--strict", action="store_true", help="exit with status 1 on any parse error")
    args = ap.parse_args(argv)

    try:
        schema = parse_file(args.file)
    except OSError as e:
        print(f"cannot read '{args.file}': {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(schema.to_dict(), indent=2))
    elif args.emit:
        print(schema.to_mermaid(), end="")
    else:
        print(_summary(schema))
    return 1 if (args.strict and schema.errors) else 0


if __name__ == "__main__":
    sys.exit(main())
