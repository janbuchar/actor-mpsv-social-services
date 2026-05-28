# MPSV Social Services Registry

Apify Actor that fetches social service listings from the [Czech MPSV (Ministry of Labour and Social Affairs) public registry](https://mpsv.gov.cz/registr-poskytovatelu-sluzeb).

## What it does

Queries the MPSV registry for the following social service categories:

- Tísňová péče
- Pečovatelská služba
- Osobní asistence
- Krizová pomoc
- Telefonická krizová pomoc
- Odlehčovací služby (terénní + pobytová)
- Domovy se zvláštním režimem

Each service record is pushed to the default dataset with provider details (IČO, name), service identifiers (IČS), facility information, and service form metadata.
