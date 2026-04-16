# AssetMGMT

Kontenerowa aplikacja Flask do ewidencji urządzeń medycznych i części zamiennych.

## Uruchomienie

1. Skopiuj plik konfiguracyjny:

```bash
cp .env.example .env
```

2. Uzupełnij w `.env` własne wartości:
- `SECRET_KEY`
- `ADMIN_PASSWORD`
- `PUBLIC_BASE_URL`
- opcjonalnie ustaw `APP_UID` i `APP_GID`, jeśli chcesz dopasować UID/GID do hosta

3. Uruchom kontener:

```bash
docker compose up --build -d
```

Aplikacja będzie dostępna pod adresem wskazanym w `PUBLIC_BASE_URL`.

## Najważniejsze cechy bezpieczeństwa

- CSRF dla formularzy POST
- limit prób logowania: 5/min/IP
- walidacja treści plików przy uploadzie
- sesja wygasa po 8 godzinach
- kontener działa jako użytkownik nieroot
- zasoby statyczne serwowane lokalnie

## Uwagi

- katalog `data/` jest montowany jako wolumen do `/data` w kontenerze
- entrypoint nadaje prawa zapisu dla `appuser` przed startem aplikacji
- plik `.env` nie powinien trafiać do repozytorium
- domyślne hasło administratora ustawiane jest z `ADMIN_PASSWORD`
