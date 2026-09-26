# UT622E remote command reference

Source: UT622 Series User Manual (V1.00), pp. 31–39, plus live probing of
firmware Ver1.3.2142. Items marked *probed* are not in the manual.

## Link
- Serial 9600 / 19200 / 38400 baud, 8N1, no flow control (CH340, COM6 here).
- Send ASCII + `\n`. Queries reply with one line ending `\r\n`; set commands reply with nothing.
- Commands are case-insensitive; the short form is the capitalised part (e.g. `FUNCtion` → `FUNC`).
- Front-panel buttons keep working while the PC is connected.
- Unit multipliers: `K` 1e3, `MA` 1e6, `M` 1e-3, `U` 1e-6, `N` 1e-9, `P` 1e-12
  (note `M` = milli, `MA` = mega).

## Common commands
| Command | Meaning |
|---|---|
| `*IDN?` | `UNI_T,UT622E,<serial>,<firmware>` |
| `*RST` | Reset measurement params (not system settings); leaves tolerance/record mode |
| `*TRG` | Trigger + return result (= `TRIG` + `FETC?`) |
| `*OPC?` | Returns `1` when previous command is complete |
| `*LLO` | Lock front-panel keys (hold power 1 s to unlock) |
| `*GTL` | Unlock front-panel keys |

## Trigger
| Command | Meaning |
|---|---|
| `TRIG[:IMM]` | Single trigger (only on measurement page, not in continuous mode) |
| `TRIG:SOUR {AUTO\|INT}` | Continuous (internal) trigger |
| `TRIG:SOUR {MAN\|BUS}` | Single-shot trigger |
| `TRIG:SOUR?` | `AUTO` \| `MAN` |

## Reading
| Command | Meaning |
|---|---|
| `FETC?` | `<A>,<B>,<cmp>`: A = primary, B = secondary (NR3, SI units), cmp = `0` fail, `1` pass, `N` not compared |
| `FETC:AUTO {ON\|OFF\|1\|0}` | Meter pushes every new result without being asked |
| `FETC:AUTO?` | `ON` \| `OFF` |

`FETC?` blocks until a new result exists if the last one was already read.

## Function
| Command | Values |
|---|---|
| `FUNC:IMPA x` / `?` | `L C R Z DCR` (not allowed in record/tolerance mode) |
| `FUNC:IMPB x` / `?` | `D Q X DEG RAD ESR` |
| `FUNC:RANG n` / `?` | `0..4` = 100 kΩ / 10 kΩ / 1 kΩ / 100 Ω / 10 Ω (setting it holds the range); query returns `R0..R4` |
| `FUNC:RANG:AUTO {ON\|OFF}` / `?` | `AUTO` \| `HOLD` |
| `FUNC:EQU {SER\|PAR}` / `?` | Series / parallel equivalent circuit (not in DCR) |

## Signal
| Command | Values |
|---|---|
| `FREQ f` / `?` | `100Hz 120Hz 1kHz 10kHz 100kHz` (or `100 … 100000`); not in DCR |
| `VOLT v` / `?` | `0.1V 0.3V 1.0V`; not in DCR (`LEV?` also answers, *probed*) |
| `APER s` / `?` | `FAST`/`SHORT` (20/s), `MED` (5/s), `SLOW`/`LONG` (2/s) |

## Tolerance comparator (primary parameter only)
| Command | Values |
|---|---|
| `COMP[:STAT] {ON\|OFF}` / `?` | Tolerance mode |
| `COMP:NOM x` / `?` | Nominal value, e.g. `1.23m` (mH) or `1.23e-6` (F) |
| `COMP:TOL n` / `?` | Integer 1..20 (%) |
| `COMP:ALAR[:STAT] {OFF\|PASS\|FAIL}` / `?` | Beep condition |
| `COMP:ALAR:SOUN {SHORT\|LONG\|DUAL}` / `?` | Beep pattern |
| `COMP:ALAR:LED {ON\|OFF}` / `?` | Alarm LED |
| `COMP:COUN {ON\|OFF}` / `?` | Pass/fail counter |

## Undocumented (*probed*)
| Command | Reply |
|---|---|
| `CORR:OPEN?` | `1` (open-circuit correction active) |
| `CORR:SHOR?` | `1` (short-circuit correction active) |
| `SER?` | Serial number |

Open/short correction, recording statistics (avg/max/min) and system settings
(backlight, auto power-off, beep) are front-panel functions; no documented
remote command exists for them.
