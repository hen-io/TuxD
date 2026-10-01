LANGUAGES = ("en", "nb")

_NB = {
    "Select stack...": "Velg stack...",
    "Select action...": "Velg handling...",
    "All stacks": "Alle stacks",
    "Update": "Oppdater",
    "Start": "Start",
    "Stop": "Stopp",
    "Health Check": "Helsesjekk",
    "Health": "Helse",
    "TugBoat Stack": "TugBoat-stack",
    "TugBoat Action": "TugBoat-handling",
    "TugBoat Execute": "TugBoat utfør",
    "TugBoat enabled": "TugBoat aktivert",
    "CPU load": "CPU-last",
    "Host CPU Load": "Vert CPU-last",
    "Memory used percent": "Minnebruk prosent",
    "Update status": "Oppdateringsstatus",
    "Startup time": "Oppstartstid",
    "MQTT Base Topic": "MQTT-basisemne",
    "Agent icon": "Agentikon",
    "Load average 1m": "Lastgjennomsnitt 1m",
    "Load average 5m": "Lastgjennomsnitt 5m",
    "Load average 15m": "Lastgjennomsnitt 15m",
    "Storage Used GB": "Lagring brukt GB",
    "Storage Free GB": "Lagring ledig GB",
    "Storage Used %": "Lagring brukt %",
    "I/O reads": "I/O lesing",
    "I/O writes": "I/O skriving",
    "I/O RW": "I/O les/skriv",
    "Network in": "Nettverk inn",
    "Network out": "Nettverk ut",
    "Network in/out": "Nettverk inn/ut",
    "Docker monitoring enabled": "Docker-overvåking aktivert",
    "Docker containers up": "Docker-containere oppe",
    "Docker containers with errors": "Docker-containere med feil",
    "Host updates": "Systemoppdateringer",
    "New package version available": "Ny pakkeversjon tilgjengelig",
    "TuxD Agent Update": "TuxD-agentoppdatering",
    "Check for TuxD Agent Updates": "Se etter TuxD-agentoppdateringer",
    "TuxD Agent Release Channel": "TuxD-agent utgivelseskanal",
    "Restart TuxD": "Start TuxD på nytt",
    "Refresh TuxD Entities": "Oppdater TuxD-entiteter",
    "Force Refresh All Sensors": "Tving oppdatering av alle sensorer",
    "Error": "Feil",
    "Error Reason": "Feilårsak",
    "Busy": "Opptatt",
    "Current Job": "Gjeldende jobb",
    "High Latency": "Høy forsinkelse",
    "Terminal Input": "Terminal inndata",
    "Terminal Output": "Terminal utdata",
    "Stop Terminal Command": "Stopp terminalkommando",
}

_TABLES = {"nb": _NB}


def resolve_language(raw):
    lang = str(raw or "").strip().lower()
    return "nb" if lang in ("nb", "nn", "no") else "en"


def translate(lang, text):
    return _TABLES.get(lang, {}).get(text, text)
