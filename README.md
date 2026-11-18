# CSCE_411_Project
Final Project for CSCE 411 that does defense validation on ER diagrams


Things to check for:
* Check if UK keys are correctly added for attributes
* Check if UK keys are not present for non-unique attributes
* For 1:1 relationships, checking if FK also has UK key in child entity
* If PK, then it's implied that also unique
* For 1:m relationships, check if 'm' side doesn't have UK tag in child entity, preventing accidental 1:1 relationship

Why use Mermaid files:
* They allow diagrams to be controlled easily as plain text
* They render automaically in Markdown viewers
* They already have built-in ER diagram formatting tables
* They natively support standard relational key constraints, including Primary Keys (`PK`), Foreign Keys (`FK`), and Unique Keys (`UK`).
* They are widely adopted in real-world software engineering workflows for maintaining collaborative system documentation
