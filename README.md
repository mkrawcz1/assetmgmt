# AssetMGMT

Gotowa, kontenerowa aplikacja Flask do ewidencji urządzeń medycznych i części zamiennych.

## Funkcje
- urządzenia i modele urządzeń
- części i typy części
- hierarchiczne lokalizacje
- role: admin / edytor / czytacz
- lokalne logowanie
- logiczne usuwanie i przywracanie
- załączniki plikowe
- generowanie i druk QR dla urządzeń
- skanowanie QR z poziomu przeglądarki mobilnej
- dashboard
- import CSV z walidacją i osobnymi szablonami

## Uruchomienie
```bash
docker compose up --build
```

Aplikacja będzie dostępna pod adresem:
```text
http://localhost:5000
```

## Konto startowe
- login: `admin`
- hasło: `admin12345`

Hasło można nadpisać zmienną środowiskową `ADMIN_PASSWORD`.

## Dane trwałe
Dane SQLite, załączniki i wygenerowane kody QR są trzymane w katalogu `./data` mapowanym jako wolumen.

## Szablony importu CSV
Dostępne z poziomu panelu administracyjnego.

## Uwagi
To jest działające MVP. Nie zawiera pełnego audytu zmian, resetu hasła przez e-mail ani integracji z zewnętrznym SSO.
