"""Parser tests for the catalogue client (no Home Assistant needed)."""

from datetime import date
from pathlib import Path
import importlib.util
import sys
import types

from bs4 import BeautifulSoup

# Import api.py without importing the package __init__ (which needs Home Assistant).
PKG = Path(__file__).parents[1] / "custom_components" / "stadtbuecherei_muenster"
sys.modules.setdefault("sbm", types.ModuleType("sbm")).__path__ = [str(PKG)]
for name in ("const", "api"):
    spec = importlib.util.spec_from_file_location(f"sbm.{name}", PKG / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"sbm.{name}"] = module
    spec.loader.exec_module(module)
api = sys.modules["sbm.api"]

FIXTURE = Path(__file__).parent / "fixtures" / "kundenkonto.html"


def soup() -> BeautifulSoup:
    return BeautifulSoup(FIXTURE.read_text(encoding="utf-8"), "html.parser")


def test_parse_loans():
    loans = api.parse_loans(soup())
    assert [l.copy_id for l in loans] == ["M000000001", "M000000002"]

    first, second = loans
    assert first.title == "Ein Roman für Leser"
    assert first.author is None
    assert first.media_group == "Kinderroman"
    assert first.branch == "Hauptstelle"
    assert first.due_date == date(2026, 12, 10)
    assert first.catalogue_url == "https://open.stadt-muenster.de/Mediensuche?id=12345"
    assert first.renew_target == (
        "dnn$ctr376$MainView$tpnlLoans$ucLoansView$grdViewLoans$ctl02$BtnExtendThis"
    )

    assert second.author == "Muster, Erika"
    assert second.due_date == date(2026, 12, 3)
    assert second.renew_target is None
    assert second.cover_sources == []


def test_parse_cover_sources():
    first = api.parse_loans(soup())[0]
    assert first.cover_sources == [
        (
            "ajax",
            "/DesktopModules/OCLC.OPEN.PL.DNN.BaseLibrary/RequestCover.asmx/GetMvbCover"
            "|0$9783789112164",
        ),
        ("simple", "https://images-eu.ssl-images-amazon.com/images/P/378911216X.03.MZZZZZZZ.jpg"),
        ("simple", "/Portals/0/Bilder/cover/JROMAN.png"),
    ]


def test_form_fields_and_login_detection():
    page = soup()
    fields = api._form_fields(page)
    assert fields["__VIEWSTATE"] == "vs"
    assert fields["dnn$ctr376$MainView$tpnlLoans$ucLoansView$grdViewLoans$ctl02$CopyId"] == "M000000001"
    assert api.is_logged_in(page)

    # A password input alone (e.g. "change password") must not count as logged out.
    page.form.append(BeautifulSoup('<input name="x$txtPassword" type="password" />', "html.parser"))
    assert api.is_logged_in(page)

    login = BeautifulSoup(
        '<input name="dnn$ctr375$Login$Login_COP$txtPassword" type="password" />'
        '<div class="dnnFormMessage dnnFormValidationSummary">Die Ausweisnummer oder '
        'E-Mailadresse ist unbekannt oder das Passwort ist falsch.</div>',
        "html.parser",
    )
    assert not api.is_logged_in(login)
    assert api.login_error(login).startswith("Die Ausweisnummer")


def test_parse_date():
    assert api.parse_date("Frist: 01.02.2027") == date(2027, 2, 1)
    assert api.parse_date("") is None


GRIDS = """
<table id="x_grdViewReservations"><tr><th abbr="Titel">Titel</th><th>Verfasser</th><th>Abholort</th><th>Position</th></tr>
<tr><td>Ein Buch</td><td>Muster, Max</td><td>Hauptstelle</td><td>2</td></tr></table>
<table id="x_grdViewReadyForPickups"><tr><th>Titel</th><th>Abholort</th><th>Abholbar bis</th></tr>
<tr><td colspan="3">Keine Einträge</td></tr></table>
<table id="x_grdViewFees"><tr><th>Datum</th><th>Gebührenart</th><th>Betrag</th></tr>
<tr><td>01.11.2026</td><td>Säumnisgebühr</td><td>1,50 €</td></tr>
<tr><td>02.11.2026</td><td>Vormerkgebühr</td><td>1.000,00 €</td></tr></table>
"""


def test_parse_reservations_and_fees():
    page = BeautifulSoup(GRIDS, "html.parser")
    (res,) = api.parse_reservations(page)
    assert (res.title, res.author, res.branch, res.position, res.ready_for_pickup) == (
        "Ein Buch", "Muster, Max", "Hauptstelle", "2", False,
    )

    fees = api.parse_fees(page)
    assert [(f.reason, f.date, f.amount) for f in fees] == [
        ("Säumnisgebühr", date(2026, 11, 1), 1.5),
        ("Vormerkgebühr", date(2026, 11, 2), 1000.0),
    ]
    data = api.AccountData(loans={}, reservations=[res], fees=fees)
    assert data.fees_total == 1001.5


def test_empty_account_tables():
    assert api.parse_reservations(soup()) == []
    assert api.parse_fees(soup()) == []
