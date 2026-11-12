# Stadtbücherei Münster für Home Assistant

Custom Integration für das Kundenkonto im Online-Katalog der Stadtbücherei Münster
(<https://open.stadt-muenster.de>, OCLC OPEN). Zeigt die aktuellen Ausleihen mit Cover
und verlängert Medien per Knopfdruck.

## Installation

**HACS / Marketplace:** `https://github.com/GrisuMS/ha-stadtbuecherei-muenster` als
*Custom Repository* (Typ *Integration*) hinzufügen, „Stadtbücherei Münster“ installieren,
Home Assistant neu starten.

**Manuell:** `custom_components/stadtbuecherei_muenster` nach
`<config>/custom_components/` kopieren und Home Assistant neu starten.

Danach unter *Einstellungen → Geräte & Dienste → Integration hinzufügen* die
„Stadtbücherei Münster“ auswählen und Ausweisnummer und Passwort eingeben.
Mehrere Ausweise (z. B. Familie) lassen sich als getrennte Einträge anlegen.

## Entitäten

Pro Ausweis ein Gerät:

| Entität | Inhalt |
| --- | --- |
| `sensor.…_ausleihen` | Anzahl der Medien; Attribut `loans` mit der kompletten Liste |
| `sensor.…_nachste_frist` | Früheste Rückgabefrist |
| `sensor.…_vormerkungen` | Anzahl offener Vormerkungen; Attribut `reservations` |
| `sensor.…_abholbereit` | Anzahl abholbereiter Vormerkungen; Attribut `reservations` |
| `sensor.…_gebuhren` | Summe offener Gebühren in EUR; Attribut `fees` |

Pro ausgeliehenem Medium ein eigenes Gerät (Name = Titel, Modell = Mediengruppe):

| Entität | Inhalt |
| --- | --- |
| `image.<titel>_cover` | Cover |
| `sensor.<titel>_aktuelle_frist` | Frist als Datum; Attribute `author`, `media_group`, `branch`, `days_left`, `renewable`, `status_message` |
| `binary_sensor.<titel>_verlangerbar` | Ob die Verlängerung gerade möglich ist |
| `button.<titel>_verlangern` | Verlängert das Medium |

Geräte von zurückgegebenen Medien werden beim nächsten Abruf automatisch entfernt.
Neu ausgeliehene Medien landen automatisch im Bereich des Ausweis-Geräts. Ein Medium, das
du von Hand in einen anderen Bereich verschiebst, bleibt dort.
Das Konto wird alle 3 Stunden abgerufen; nach einer Verlängerung sofort.

## Beispiel: Erinnerung eine Woche vor Ablauf

Prüft jeden Morgen alle Fristen-Sensoren der Integration, auch die von neu ausgeliehenen
Medien, und meldet sich nur, wenn etwas in genau 7 Tagen fällig ist. `notify.sebastian`
durch das eigene Notify-Ziel ersetzen (z. B. einen Telegram-Chat).

```yaml
alias: "Stadtbücherei: Erinnerung eine Woche vor Rückgabe"
triggers:
  - trigger: time
    at: "09:00:00"
variables:
  faellig: >-
    {% set ns = namespace(items=[]) %}
    {% for e in integration_entities('stadtbuecherei_muenster')
       if e.startswith('sensor.') and state_attr(e, 'copy_id')
       and states(e) not in ['unknown', 'unavailable'] %}
      {% if (as_datetime(states(e)).date() - now().date()).days == 7 %}
        {% set ns.items = ns.items + ['• ' ~ state_attr(e, 'title')
           ~ (' (verlängerbar)' if state_attr(e, 'renewable') else ' (nicht verlängerbar)')] %}
      {% endif %}
    {% endfor %}
    {{ ns.items }}
conditions:
  - condition: template
    value_template: "{{ faellig | count > 0 }}"
actions:
  - action: notify.send_message
    target:
      entity_id: notify.sebastian
    data:
      title: "📚 Stadtbücherei"
      message: >-
        In einer Woche ({{ (now() + timedelta(days=7)).strftime('%d.%m.%Y') }}) fällig:
        {{ faellig | join('\n') }}
```

## Wie es funktioniert

Der Katalog hat keine offizielle API. Die Integration verhält sich wie der Browser:

- **Login** und **Verlängern** sind ASP.NET-Postbacks auf `/Kundenkonto`.
- **Verlängerbar** kommt aus `PatronAccountService.asmx/IsCatalogueCopyExtendable`.
- **Cover** kommen aus `RequestCover.asmx/GetMvbCover`. Fallback: Amazon und dann
  das Platzhalterbild der Mediengruppe.

Ändert die Bücherei ihre Seite, kann die Integration brechen.

## Tests

```sh
pip install beautifulsoup4 aiohttp pytest
pytest tests
```
