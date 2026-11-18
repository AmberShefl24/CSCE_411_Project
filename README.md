# CSCE_411_Project
Final Project for CSCE 411 that does defense validation on ER diagrams


Things to check for:
* Check if UK keys are correctly added for attributes
* Check if UK keys are not present for non-unique attributes
* For 1:1 relationships, checking if FK also has UK key in child entity
* If PK, then it's implied that also unique
* For 1:m relationships, check if 'm' side doesn't have UK tag in child entity, preventing accidental 1:1 relationship
