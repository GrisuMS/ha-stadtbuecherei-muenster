"""Client for the OCLC OPEN catalogue of the Stadtbücherei Münster.

The catalogue has no public API. It is an ASP.NET WebForms (DotNetNuke) site,
so every action is a form postback carrying the page's hidden state fields
(__VIEWSTATE, __EVENTVALIDATION, ...). Renewability and covers are loaded by
the page via small JSON web services, which this client calls directly.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup, Tag

from .const import ACCOUNT_PATH, BASE_URL

_LOGGER = logging.getLogger(__name__)

EXTENDABLE_SERVICE = (
    "/DesktopModules/OCLC.OPEN.PL.DNN.PatronAccountModule/"
    "PatronAccountService.asmx/IsCatalogueCopyExtendable"
)
EXTENDABLE_RESOURCE_FILE = (
    "~/DesktopModules/OCLC.OPEN.PL.DNN.PatronAccountModule/"
    "App_LocalResources/PatronAccountModule.resx"
)

# Identify honestly as this integration; Home Assistant's default user agent
# is replaced per request, as its aiohttp helper recommends for integrations.
HEADERS = {
    "User-Agent": (
        "ha-stadtbuecherei-muenster/0.1.8 "
        "(+https://github.com/GrisuMS/ha-stadtbuecherei-muenster)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9",
}
JSON_HEADERS = {
    **HEADERS,
    "Accept": "application/json",
    "Content-Type": "application/json;charset=utf-8",
}

_POSTBACK_RE = re.compile(r"__doPostBack\('([^']+)','([^']*)'\)")
_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")


class LibraryError(Exception):
    """Generic error talking to the catalogue."""


class AuthenticationError(LibraryError):
    """Login was rejected."""


class RenewalError(LibraryError):
    """A renewal did not go through."""


@dataclass
class Loan:
    """One borrowed medium."""

    copy_id: str
    title: str
    author: str | None
    media_group: str | None
    branch: str | None
    due_date: date | None
    catalogue_url: str | None = None
    renewable: bool | None = None
    renewal_text: str | None = None
    status_message: str | None = None
    cover_sources: list[tuple[str, str]] = field(default_factory=list)
    renew_target: str | None = None


@dataclass
class Reservation:
    """A reserved medium, either still waiting or ready for pickup."""

    title: str
    author: str | None
    media_group: str | None
    branch: str | None
    position: str | None
    date: date | None
    ready_for_pickup: bool
    columns: dict[str, str]


@dataclass
class Fee:
    """One open fee."""

    reason: str | None
    title: str | None
    date: date | None
    amount: float | None
    columns: dict[str, str]


@dataclass
class AccountData:
    """Everything the integration reads from the account page."""

    loans: dict[str, Loan]
    reservations: list[Reservation]
    fees: list[Fee]

    @property
    def fees_total(self) -> float:
        return round(sum(fee.amount or 0 for fee in self.fees), 2)


def parse_amount(text: str | None) -> float | None:
    """Parse a German money amount like '1,50 €'."""
    if not text or not (match := re.search(r"-?\d{1,3}(?:\.\d{3})*,\d{2}|-?\d+", text)):
        return None
    return float(match.group(0).replace(".", "").replace(",", "."))


def parse_date(text: str | None) -> date | None:
    """Parse a German dd.mm.yyyy date."""
    if not text or not (match := _DATE_RE.search(text)):
        return None
    day, month, year = match.groups()
    return date(int(year), int(month), int(day))


def _cell_value(cell: Tag) -> str | None:
    """Return the value of a table cell, skipping the mobile-only label span."""
    for label in cell.select(".oclc-module-label"):
        label.extract()
    text = " ".join(cell.get_text(" ", strip=True).split())
    return text or None


def parse_cover_sources(raw: str | None) -> list[tuple[str, str]]:
    """Parse the img data-sources attribute into (method, url/data) pairs.

    Format: entries separated by "|c|", fields by "|a|":
    ``SetCoverByAjax|a|<service url>|a|<data>|c|SetSimpleCover|a|<image url>|a|<link>``
    """
    sources: list[tuple[str, str]] = []
    for entry in (raw or "").split("|c|"):
        parts = entry.split("|a|")
        if len(parts) < 2 or not parts[1]:
            continue
        method = parts[0]
        if method.endswith("SetCoverByAjax") and len(parts) >= 3:
            sources.append(("ajax", f"{parts[1]}|{parts[2]}"))
        elif method.endswith("SetSimpleCover"):
            sources.append(("simple", parts[1]))
    return sources


def is_logged_in(soup: BeautifulSoup) -> bool:
    """Return True if the page shows the patron account.

    Checked via the account's own fields: the logged-in page can still
    contain password inputs (e.g. for changing the password).
    """
    return (
        soup.find("input", attrs={"name": re.compile(r"\$PatronRndId$")}) is not None
        or soup.find("table", id=re.compile(r"grdViewLoans$")) is not None
    )


def login_error(soup: BeautifulSoup) -> str | None:
    """Return the validation message the login form shows, if any."""
    summary = soup.select_one(".dnnFormValidationSummary, .dnnFormError")
    if summary is None:
        return None
    return " ".join(summary.get_text(" ", strip=True).split()) or None


def parse_loans(soup: BeautifulSoup) -> list[Loan]:
    """Extract all loans from the account page."""
    table = soup.find("table", id=re.compile(r"grdViewLoans$"))
    if table is None:
        return []

    loans: list[Loan] = []
    for row in table.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 8:
            continue  # header or pager row

        copy_input = cells[7].find("input", attrs={"name": re.compile(r"\$CopyId$")})
        if copy_input is None:
            copy_input = cells[0].find("input", attrs={"name": re.compile(r"\$CopyIdChx$")})
        if copy_input is None or not copy_input.get("value"):
            continue

        title_link = cells[2].find("a")
        catalogue_url = (
            urljoin(BASE_URL, title_link["href"])
            if title_link and title_link.get("href")
            else None
        )

        img = cells[1].find("img")
        cover_sources = parse_cover_sources(img.get("data-sources") if img else None)

        renew_target = None
        if (link := cells[7].select_one("a.oclc-patronaccountmodule-extendThis")) and (
            match := _POSTBACK_RE.search(link.get("href", ""))
        ):
            renew_target = match.group(1)

        loans.append(
            Loan(
                copy_id=copy_input["value"],
                title=_cell_value(cells[2]) or "?",
                author=_cell_value(cells[3]),
                media_group=_cell_value(cells[4]),
                branch=_cell_value(cells[5]),
                due_date=parse_date(_cell_value(cells[6])),
                catalogue_url=catalogue_url,
                cover_sources=cover_sources,
                renew_target=renew_target,
            )
        )
    return loans


def _parse_grid(soup: BeautifulSoup, id_suffix: str) -> list[dict[str, str]]:
    """Read an account table as one {header: value} dict per data row.

    Used for tables whose column order is not fixed in code; empty tables
    render a single "no entries" row, which has fewer cells and is skipped.
    """
    table = soup.find("table", id=re.compile(rf"{id_suffix}$"))
    if table is None:
        return []
    headers = [
        " ".join((th.get("abbr") or th.get_text(" ", strip=True)).split())
        for th in table.find_all("th")
    ]
    rows = []
    for row in table.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if not headers or len(cells) != len(headers):
            continue
        values = {
            header: value
            for header, cell in zip(headers, cells)
            if header and (value := _cell_value(cell))
        }
        if values:
            rows.append(values)
    return rows


def _pick(columns: dict[str, str], *keywords: str) -> str | None:
    """Return the first column whose header contains one of the keywords."""
    for keyword in keywords:
        for header, value in columns.items():
            if keyword in header.lower():
                return value
    return None


def parse_reservations(soup: BeautifulSoup) -> list[Reservation]:
    """Extract reservations and media ready for pickup."""
    result = []
    for id_suffix, ready in (
        ("grdViewReservations", False),
        ("grdViewReadyForPickups", True),
    ):
        for columns in _parse_grid(soup, id_suffix):
            result.append(
                Reservation(
                    title=_pick(columns, "titel") or "?",
                    author=_pick(columns, "verfasser"),
                    media_group=_pick(columns, "mediengruppe"),
                    branch=_pick(columns, "abhol", "zweigstelle"),
                    position=_pick(columns, "position", "platz", "rang"),
                    date=parse_date(_pick(columns, "frist", "bis", "datum")),
                    ready_for_pickup=ready,
                    columns=columns,
                )
            )
    return result


def parse_fees(soup: BeautifulSoup) -> list[Fee]:
    """Extract open fees."""
    return [
        Fee(
            reason=_pick(columns, "grund", "gebührenart", "art", "bezeichnung", "beschreibung"),
            title=_pick(columns, "titel"),
            date=parse_date(_pick(columns, "datum")),
            amount=parse_amount(_pick(columns, "betrag", "summe", "gebühr", "€")),
            columns=columns,
        )
        for columns in _parse_grid(soup, "grdViewFees")
    ]


def _describe_set_cookies(resp: aiohttp.ClientResponse) -> list[str]:
    """Describe Set-Cookie headers for the log: names and attributes, no values."""
    described = []
    for header in resp.headers.getall("Set-Cookie", []):
        name_value, *attrs = (part.strip() for part in header.split(";"))
        name, _, value = name_value.partition("=")
        attr_names = []
        for attr in filter(None, attrs):
            key = attr.split("=", 1)[0].lower()
            if key == "expires" and (year := re.search(r"\d{4}", attr)):
                key = f"expires {year.group(0)}"
            attr_names.append(key)
        described.append(f"{name}(len={len(value)}; {','.join(attr_names)})")
    return described


def _form_fields(soup: BeautifulSoup) -> dict[str, str]:
    """Collect the hidden state fields a postback has to send back."""
    form = soup.find("form", id="Form") or soup
    return {
        inp["name"]: inp.get("value", "")
        for inp in form.find_all("input", attrs={"type": "hidden"})
        if inp.get("name")
    }


def _page_messages(soup: BeautifulSoup) -> list[str]:
    """Collect visible status/error messages from a page."""
    messages = []
    for el in soup.select(
        ".dnnFormMessage, .oclc-module-message, [class*='Message'], [id$='lblMessage']"
    ):
        text = " ".join(el.get_text(" ", strip=True).split())
        if text and text not in messages:
            messages.append(text)
    return messages


class StadtbuechereiClient:
    """Async client for one library account."""

    def __init__(
        self, session: aiohttp.ClientSession, username: str, password: str
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._lock = asyncio.Lock()
        self._patron_rnd_id: str | None = None

    async def _get(self, path: str) -> BeautifulSoup:
        async with self._session.get(urljoin(BASE_URL, path), headers=HEADERS) as resp:
            resp.raise_for_status()
            return BeautifulSoup(await resp.text(), "html.parser")

    async def _postback(
        self, soup: BeautifulSoup, data: dict[str, str]
    ) -> BeautifulSoup:
        payload = {"__EVENTTARGET": "", "__EVENTARGUMENT": ""}
        payload.update(_form_fields(soup))
        payload.update(data)
        async with self._session.post(
            urljoin(BASE_URL, ACCOUNT_PATH), data=payload, headers=HEADERS
        ) as resp:
            resp.raise_for_status()
            return BeautifulSoup(await resp.text(), "html.parser")

    async def _login(self, login_page: BeautifulSoup) -> BeautifulSoup:
        def field(suffix: str) -> str:
            el = login_page.find("input", attrs={"name": re.compile(rf"\${suffix}$")})
            if el is None:
                raise LibraryError(f"Login form field {suffix} not found")
            return el["name"]

        payload = {"__EVENTTARGET": "", "__EVENTARGUMENT": ""}
        payload.update(_form_fields(login_page))
        payload.update(
            {
                field("txtUsername"): self._username,
                field("txtPassword"): self._password,
                field("cmdLogin"): "Anmeldung",
            }
        )

        # Follow redirects by hand to record which cookies each hop sets.
        hops: list[str] = []
        method, url, data = "POST", urljoin(BASE_URL, ACCOUNT_PATH), payload
        for _ in range(6):
            async with self._session.request(
                method, url, data=data, headers=HEADERS, allow_redirects=False
            ) as resp:
                hops.append(
                    f"{method} {resp.url.path}?{resp.url.query_string} -> {resp.status}, "
                    f"set-cookie {_describe_set_cookies(resp)}, "
                    f"jar {sorted(c.key for c in self._session.cookie_jar)}"
                )
                location = resp.headers.get("Location")
                if resp.status in (301, 302, 303, 307) and location:
                    method, url, data = "GET", urljoin(str(resp.url), location), None
                    continue
                resp.raise_for_status()
                soup = BeautifulSoup(await resp.text(), "html.parser")
                break
        else:
            raise LibraryError(f"Too many redirects after login: {hops}")

        if is_logged_in(soup):
            return soup
        if message := login_error(soup):
            raise AuthenticationError(message)
        raise LibraryError(
            f"Not logged in after login. Hops: {' | '.join(hops)}; "
            f"final page: {self._diagnose(soup)}"
        )

    @staticmethod
    def _diagnose(soup: BeautifulSoup) -> str:
        """Describe a page for the log, without any form values."""
        title = soup.title.get_text(strip=True) if soup.title else "?"
        login_form = (
            soup.find("input", attrs={"name": re.compile(r"Login.*\$txtPassword$")})
            is not None
        )
        return (
            f"title='{title}', login form={login_form}, "
            f"messages={[m[:150] for m in _page_messages(soup)[:3]]}"
        )

    async def _account_page(self) -> BeautifulSoup:
        """Return the account page, logging in first if the session expired."""
        soup = await self._get(ACCOUNT_PATH)
        if not is_logged_in(soup):
            soup = await self._login(soup)
        rnd = soup.find("input", attrs={"name": re.compile(r"\$PatronRndId$")})
        self._patron_rnd_id = rnd.get("value") if rnd else None
        return soup

    async def async_validate_login(self) -> None:
        """Log in once; raises AuthenticationError on bad credentials."""
        async with self._lock:
            await self._account_page()

    async def async_get_account(self) -> AccountData:
        """Fetch loans (including renewability), reservations and fees."""
        async with self._lock:
            soup = await self._account_page()
            loans = parse_loans(soup)
            await self._fill_renewability(loans)
            return AccountData(
                loans={loan.copy_id: loan for loan in loans},
                reservations=parse_reservations(soup),
                fees=parse_fees(soup),
            )

    async def _fill_renewability(self, loans: list[Loan]) -> None:
        if not loans or not self._patron_rnd_id:
            return
        body = (
            "{'portalId':'0', 'userName':'%s', 'copyIds':'%s', 'culture':'de-DE', "
            "'localResourceFile':'%s'}"
        ) % (
            self._patron_rnd_id,
            ",".join(loan.copy_id for loan in loans),
            EXTENDABLE_RESOURCE_FILE,
        )
        try:
            async with self._session.post(
                urljoin(BASE_URL, EXTENDABLE_SERVICE),
                data=body,
                headers=JSON_HEADERS,
            ) as resp:
                resp.raise_for_status()
                result = await resp.json(content_type=None)
        except (aiohttp.ClientError, ValueError) as err:
            _LOGGER.warning("Could not load renewability: %s", err)
            return

        by_id = {loan.copy_id: loan for loan in loans}
        for item in result.get("d") or []:
            if (loan := by_id.get(str(item.get("CopyId")))) is None:
                continue
            loan.renewable = bool(item.get("IsExtendable"))
            loan.renewal_text = item.get("ExtendText") or None
            loan.status_message = (
                item.get("StatusMessages") or item.get("DelayText") or None
            )

    async def async_get_cover(self, loan: Loan) -> tuple[bytes, str] | None:
        """Return (image bytes, content type) for the first cover source that works."""
        for kind, source in loan.cover_sources:
            try:
                if kind == "ajax":
                    image = await self._cover_from_service(source)
                else:
                    image = await self._cover_from_url(source)
            except (aiohttp.ClientError, ValueError) as err:
                _LOGGER.debug("Cover source %s failed: %s", source, err)
                continue
            if image:
                return image
        return None

    async def _cover_from_service(self, source: str) -> tuple[bytes, str] | None:
        url, data = source.split("|", 1)
        async with self._session.post(
            urljoin(BASE_URL, url),
            data="{'data':'%s'}" % data,
            headers=JSON_HEADERS,
        ) as resp:
            resp.raise_for_status()
            result = await resp.json(content_type=None)
        cover_url = (result.get("d") or {}).get("CoverUrl")
        if not cover_url:
            return None
        if cover_url.startswith("data:"):
            header, _, payload = cover_url.partition(",")
            content_type = header[5:].split(";")[0] or "image/jpeg"
            return base64.b64decode(payload), content_type
        return await self._cover_from_url(cover_url)

    async def _cover_from_url(self, url: str) -> tuple[bytes, str] | None:
        async with self._session.get(urljoin(BASE_URL, url), headers=HEADERS) as resp:
            if resp.status != 200 or not resp.content_type.startswith("image/"):
                return None
            content = await resp.read()
        # Amazon answers unknown ISBNs with a 1x1 placeholder GIF.
        if len(content) < 200:
            return None
        return content, resp.content_type

    async def async_renew(self, copy_id: str) -> Loan:
        """Renew one loan and return its updated state.

        Clicking "Verlängern" in a row posts back and opens a confirmation
        popup; its "Verlängerung durchführen" button performs the renewal.
        """
        async with self._lock:
            soup = await self._account_page()
            before = {loan.copy_id: loan for loan in parse_loans(soup)}
            if (loan := before.get(copy_id)) is None:
                raise RenewalError(f"Loan {copy_id} not found")
            if not loan.renew_target:
                raise RenewalError(f"No renewal link for {loan.title}")

            soup = await self._postback(soup, {"__EVENTTARGET": loan.renew_target})
            if not is_logged_in(soup):
                raise LibraryError("Session expired during renewal")

            confirm = soup.find(
                "input", attrs={"name": re.compile(r"loansExtensionPopup\$btnDefault$")}
            )
            if confirm is not None and not self._due_date_changed(soup, loan):
                soup = await self._postback(
                    soup, {confirm["name"]: confirm.get("value", "")}
                )

            messages = _page_messages(soup)
            soup = await self._get(ACCOUNT_PATH)
            after = {item.copy_id: item for item in parse_loans(soup)}
            renewed = after.get(copy_id)
            if renewed is None or renewed.due_date == loan.due_date:
                raise RenewalError(
                    "; ".join(messages) or f"Due date of {loan.title} unchanged"
                )
            _LOGGER.info(
                "Renewed %s: %s -> %s", loan.title, loan.due_date, renewed.due_date
            )
            return renewed

    @staticmethod
    def _due_date_changed(soup: BeautifulSoup, loan: Loan) -> bool:
        for item in parse_loans(soup):
            if item.copy_id == loan.copy_id:
                return item.due_date != loan.due_date
        return False
