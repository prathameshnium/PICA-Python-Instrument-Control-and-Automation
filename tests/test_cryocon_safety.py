"""Tests for the two Cryo-con Model 34 modules.

Both are driven against a fake VISA instrument that records every byte sent,
so what matters can be asserted directly: that the passive monitor writes
nothing at all, that the direct-control module refuses to send heater and
loop commands to anything that is not a Cryo-con, and that neither module
ever sends *RST (on a Cryo-con that is a ~15 s hardware reset to power-up
defaults, which would drop a running experiment's control loop).

Runnable as plain Python as well as under pytest.
"""

import importlib.util
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

SENSING_PATH = os.path.join(REPO_ROOT, "pica", "cryocon", "T_Sensing_CC34_GUI.py")
CONTROL_PATH = os.path.join(REPO_ROOT, "pica", "cryocon",
                            "T_Control_CC34_DirectControl_GUI.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sensing = _load("cryocon_sensing_under_test", SENSING_PATH)
control = _load("cryocon_control_under_test", CONTROL_PATH)

SENSING_SOURCE = open(SENSING_PATH, encoding="utf-8").read()
CONTROL_SOURCE = open(CONTROL_PATH, encoding="utf-8").read()

CRYOCON_IDN = "Cryocon,Model 34,204683,3.18A"
LAKESHORE_IDN = "LSCI,MODEL350,LSA1234,1.5"


class FakeCryocon:
    """Records every write and query. Answers the Cryo-con queries used."""

    def __init__(self, idn=CRYOCON_IDN, answers=None):
        self.idn = idn
        self.answers = {"*IDN?": idn}
        self.answers.update(answers or {})
        self.writes = []
        self.queries = []
        self.closed = False
        self.timeout = None

    def write(self, command):
        self.writes.append(command)

    def query(self, command):
        self.queries.append(command)
        if command in self.answers:
            return self.answers[command]
        raise IOError(f"unexpected query {command!r}")

    def close(self):
        self.closed = True

    # Everything the instrument was ever sent, in order.
    @property
    def traffic(self):
        return self.writes + self.queries


class FakeRM:
    def __init__(self, instrument):
        self.instrument = instrument
        self.resources = ("GPIB1::12::INSTR",)

    def list_resources(self):
        return self.resources

    def open_resource(self, resource):
        return self.instrument

    def close(self):
        pass


class _PatchVisa:
    """Point a module's pyvisa at a fake instrument."""

    def __init__(self, module, instrument):
        self.module = module
        self.instrument = instrument

    def __enter__(self):
        self._old = getattr(self.module, "pyvisa", None)
        rm = FakeRM(self.instrument)

        class _FakeVisa:
            ResourceManager = staticmethod(lambda: rm)

        self.module.pyvisa = _FakeVisa
        return rm

    def __exit__(self, *exc):
        self.module.pyvisa = self._old
        return False


# ------------------------------------------------------ identity matching

def test_both_modules_recognise_every_cryocon_spelling():
    for idn in ("Cryocon,34,204683,3.18A",
                "Cryocon,Model 34,204683,3.18A",
                "Cryocon Model 34 Rev 3.18A",
                "Cryo-con,Model 34,1,1",
                "CRYOCON,32,1,1"):
        assert sensing.is_cryocon_idn(idn), idn
        assert control.is_cryocon_idn(idn), idn


def test_neither_module_mistakes_another_instrument_for_a_cryocon():
    for idn in (LAKESHORE_IDN,
                "KEITHLEY INSTRUMENTS INC.,MODEL 2400,1234,C32",
                "Stanford_Research_Systems,SR830,1,1",
                "", "None"):
        assert not sensing.is_cryocon_idn(idn), idn
        assert not control.is_cryocon_idn(idn), idn


# --------------------------------------- passive monitor: writes nothing

def test_the_passive_monitor_connects_without_writing_anything():
    fake = FakeCryocon()
    with _PatchVisa(sensing, fake):
        backend = sensing.Cryocon34_Backend("GPIB1::12::INSTR")
    assert fake.writes == [], f"passive monitor wrote {fake.writes}"
    assert backend.idn == CRYOCON_IDN


def test_the_passive_monitor_refuses_a_non_cryocon():
    fake = FakeCryocon(idn=LAKESHORE_IDN)
    with _PatchVisa(sensing, fake):
        try:
            sensing.Cryocon34_Backend("GPIB1::12::INSTR")
        except ValueError as exc:
            assert "not a Cryo-con" in str(exc)
            assert LAKESHORE_IDN in str(exc)
        else:
            raise AssertionError("connected to a Lakeshore as if it were a Cryocon")
    assert fake.writes == []
    assert fake.closed is True, "the rejected session was left open"


def test_the_passive_monitor_checks_the_channel_units_by_query_only():
    fake = FakeCryocon(answers={"INPUT A:UNITS?": "K"})
    with _PatchVisa(sensing, fake):
        backend = sensing.Cryocon34_Backend("GPIB1::12::INSTR")
        backend.configure_for_monitoring("A")
    assert fake.writes == []
    assert "INPUT A:UNITS?" in fake.queries


def test_a_channel_left_in_celsius_is_rejected_not_logged():
    fake = FakeCryocon(answers={"INPUT A:UNITS?": "C"})
    with _PatchVisa(sensing, fake):
        backend = sensing.Cryocon34_Backend("GPIB1::12::INSTR")
        try:
            backend.configure_for_monitoring("A")
        except ValueError as exc:
            assert "Kelvin" in str(exc)
        else:
            raise AssertionError("a Celsius channel was accepted as Kelvin")


def test_reading_a_temperature_is_a_single_query():
    fake = FakeCryocon(answers={"INPUT? A": " 77.35 "})
    with _PatchVisa(sensing, fake):
        backend = sensing.Cryocon34_Backend("GPIB1::12::INSTR")
        fake.queries.clear()
        assert backend.get_temperature("A") == 77.35
    assert fake.queries == ["INPUT? A"]
    assert fake.writes == []


def test_a_faulted_sensor_raises_instead_of_returning_junk():
    fake = FakeCryocon(answers={"INPUT? A": "........"})
    with _PatchVisa(sensing, fake):
        backend = sensing.Cryocon34_Backend("GPIB1::12::INSTR")
        try:
            backend.get_temperature("A")
        except ValueError as exc:
            assert "sensor fault" in str(exc)
        else:
            raise AssertionError("a faulted sensor reading was accepted")


def test_closing_the_passive_monitor_sends_nothing():
    fake = FakeCryocon()
    with _PatchVisa(sensing, fake):
        backend = sensing.Cryocon34_Backend("GPIB1::12::INSTR")
        before = list(fake.traffic)
        backend.close()
    assert fake.traffic == before, "close() talked to the instrument"
    assert fake.closed is True


# --------------------------------------- direct control: identity guard

def test_direct_control_refuses_to_drive_a_non_cryocon():
    fake = FakeCryocon(idn=LAKESHORE_IDN)
    backend = control.Cryocon34Backend()
    with _PatchVisa(control, fake):
        backend.rm = control.pyvisa.ResourceManager()
        try:
            backend.connect("GPIB1::12::INSTR")
        except ConnectionError as exc:
            assert "not a Cryo-con" in str(exc)
            assert "Refusing to send control commands" in str(exc)
        else:
            raise AssertionError("would have driven a heater on a Lakeshore")
    assert fake.writes == []
    assert backend.cryocon is None, "the rejected session stayed connected"


def test_direct_control_accepts_a_real_cryocon():
    fake = FakeCryocon()
    backend = control.Cryocon34Backend()
    with _PatchVisa(control, fake):
        backend.rm = control.pyvisa.ResourceManager()
        idn = backend.connect("GPIB1::12::INSTR")
    assert idn == CRYOCON_IDN
    assert fake.writes == [], "connect() wrote to the instrument"


def test_disconnect_is_non_destructive():
    fake = FakeCryocon()
    backend = control.Cryocon34Backend()
    with _PatchVisa(control, fake):
        backend.rm = control.pyvisa.ResourceManager()
        backend.connect("GPIB1::12::INSTR")
        backend.disconnect()
    assert fake.writes == [], "disconnect sent commands to the instrument"
    assert fake.closed is True


# --------------------------------------------- direct control: SCPI form

def _connected_backend(fake=None):
    fake = fake or FakeCryocon()
    backend = control.Cryocon34Backend()
    with _PatchVisa(control, fake):
        backend.rm = control.pyvisa.ResourceManager()
        backend.connect("GPIB1::12::INSTR")
    fake.writes.clear()
    return backend, fake


def test_pid_is_one_compound_command_on_the_loop_path():
    backend, fake = _connected_backend()
    cmd = backend.set_pid(1, 0.2, 1, 0)
    assert cmd == "LOOP 1:PGAIN 0.2;IGAIN 1;DGAIN 0"
    assert fake.writes == [cmd]


def test_an_immediate_setpoint_leaves_ramp_mode_first():
    """Setting SETPT without clearing RampP would ramp instead of jumping."""
    backend, fake = _connected_backend()
    cmd = backend.set_setpoint_immediate(1, 300)
    assert cmd == "LOOP 1:TYPE PID;SETPT 300"
    assert fake.writes == [cmd]


def test_a_ramped_setpoint_sets_the_rate_before_the_setpoint():
    """Rate after setpoint would let the loop take off at the old rate."""
    backend, _fake = _connected_backend()
    cmd = backend.set_setpoint_with_ramp(1, 100, 0.5)
    assert cmd.index("RATE") < cmd.index("SETPT")
    assert cmd == "LOOP 1:RATE 0.5;TYPE RampP;SETPT 100"


def test_heater_range_and_load_are_loop_1_only():
    backend, _fake = _connected_backend()
    assert backend.set_range("Low") == "LOOP 1:RANGE Low"
    assert backend.set_load(50) == "LOOP 1:LOAD 50"


def test_control_and_stop_are_the_documented_bare_commands():
    backend, fake = _connected_backend()
    assert backend.control_engage() == "CONTROL"
    assert backend.control_stop() == "STOP"
    assert fake.writes == ["CONTROL", "STOP"]


def test_loop_source_is_written_as_a_channel_name():
    backend, _fake = _connected_backend()
    assert backend.set_loop_source(1, "B") == "LOOP 1:SOURCE CHB"


# ---------------------------------------- direct control: input validation

def test_out_of_range_values_never_reach_the_instrument():
    backend, fake = _connected_backend()
    bad = [
        (backend.set_pid, (1, 1001, 1, 0)),
        (backend.set_pid, (1, 1, -1, 0)),
        (backend.set_setpoint_immediate, (1, 1001)),
        (backend.set_setpoint_immediate, (1, -1)),
        (backend.set_setpoint_with_ramp, (1, 300, 0)),
        (backend.set_setpoint_with_ramp, (1, 300, 101)),
        (backend.set_rate, (1, 101)),
        (backend.set_max_power, (1, 101)),
        (backend.set_max_setpoint, (1, 1001)),
        (backend.set_manual_power, (1, 101)),
        (backend.set_range, ("Blazing",)),
        (backend.set_load, (100,)),
        (backend.set_loop_type, (1, "Turbo")),
        (backend.set_loop_source, (1, "Z")),
        (backend.set_pid, (3, 1, 1, 0)),
    ]
    for fn, args in bad:
        try:
            fn(*args)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{fn.__name__}{args} was accepted")
    assert fake.writes == [], f"a rejected value still reached the bus: {fake.writes}"


def test_only_loops_1_and_2_exist():
    assert control.Cryocon34Backend.LOOPS == ['1', '2']
    for bad_loop in (0, 3, "A", None):
        try:
            control.Cryocon34Backend._check_loop(bad_loop)
        except ValueError:
            pass
        else:
            raise AssertionError(f"loop {bad_loop!r} was accepted")


def test_a_backend_with_no_connection_refuses_to_write():
    backend = control.Cryocon34Backend()
    try:
        backend.set_setpoint_immediate(1, 300)
    except ConnectionError:
        pass
    else:
        raise AssertionError("wrote a setpoint with no connection open")


# ---------------------------------------------------------- *RST is banned

def _string_literals(source):
    """Every string literal in a module, so comments and prose are excluded."""
    import ast
    return [node.value for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)]


def _command_literals(source):
    """String literals passed to a write()/query() call anywhere in a module."""
    import ast
    out = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", getattr(node.func, "id", ""))
        if name not in ("write", "query", "_write", "_query"):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                out.append(arg.value)
            elif isinstance(arg, ast.JoinedStr):
                out.append("".join(
                    v.value for v in arg.values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str)))
    return out


def test_the_passive_module_never_sends_rst_or_cls():
    """On a Cryo-con *RST is a ~15 s hardware reset to power-up defaults."""
    for command in _command_literals(SENSING_SOURCE):
        assert "*RST" not in command, f"passive monitor sends {command!r}"
        assert "*CLS" not in command, f"passive monitor sends {command!r}"


def test_rst_in_the_control_module_lives_only_in_its_own_reset_method():
    """No other code path may reach a 15 s hardware reset by accident."""
    import ast
    tree = ast.parse(CONTROL_SOURCE)
    senders = []
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef):
            continue
        for command in _command_literals(ast.unparse(func)):
            if "*RST" in command:
                senders.append(func.name)
    assert senders == ["reset"], senders


def test_the_hardware_reset_button_asks_twice_before_sending_rst():
    """A 15 s reset must never be one stray click away."""
    import ast
    tree = ast.parse(CONTROL_SOURCE)
    for func in ast.walk(tree):
        if not (isinstance(func, ast.FunctionDef)
                and "backend.reset" in ast.unparse(func)):
            continue
        body = ast.unparse(func)
        assert body.count("askyesno") >= 2,             f"{func.name} sends *RST behind fewer than two confirmations"
        assert "15" in body, f"{func.name} does not warn about the 15 s outage"
        return
    raise AssertionError("nothing in the control GUI calls backend.reset")


def test_the_passive_module_never_sends_a_control_command():
    """A monitor that could STOP a loop would be a monitor no longer."""
    forbidden = ("CONTROL", "STOP", "LOOP ", "PGAIN", "IGAIN", "DGAIN",
                 "SETPT", "RANGE ", "PMANUAL", "MAXPWR")
    for command in _command_literals(SENSING_SOURCE):
        for bad in forbidden:
            assert bad not in command.upper(),                 f"passive monitor would send {command!r}"


def test_the_passive_module_only_ever_queries():
    """Every command it does send is a query, ending in '?'."""
    import ast
    for node in ast.walk(ast.parse(SENSING_SOURCE)):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", "")
        if name != "write":
            continue
        # File writes are fine; instrument writes are not.
        target = getattr(node.func.value, "id", "") or             getattr(getattr(node.func.value, "attr", None), "__str__", str)()
        assert "instrument" not in str(target).lower(),             "the passive monitor writes to the instrument"


def test_neither_module_hardcodes_the_address_as_the_only_route():
    """The factory address may be a hint, but identity must decide."""
    for module in (sensing, control):
        assert hasattr(module, "is_cryocon_idn")
        # Address 23 since 3 Sep 2026 (12 collided with the L340/L350/6221);
        # board-independent so a Cryocon on GPIB0 or GPIB1 is both found.
        assert module.CRYOCON_ADDRESS_HINT == "::23::INSTR"
    assert "identify_resources" in SENSING_SOURCE
    assert "identify_resources" in CONTROL_SOURCE


def test_serial_ports_are_not_probed_during_a_cryocon_scan():
    assert sensing.PROBE_RESOURCE_PREFIXES == ("GPIB", "USB", "TCPIP")
    assert control.PROBE_RESOURCE_PREFIXES == ("GPIB", "USB", "TCPIP")

    class Recorder(FakeRM):
        def __init__(self):
            FakeRM.__init__(self, FakeCryocon())
            self.opened = []

        def open_resource(self, resource):
            self.opened.append(resource)
            return self.instrument

    rm = Recorder()
    found = sensing.identify_resources(rm, ["ASRL1::INSTR", "GPIB1::12::INSTR"])
    assert rm.opened == ["GPIB1::12::INSTR"]
    assert found == {"GPIB1::12::INSTR": CRYOCON_IDN}


def test_identify_resources_survives_a_silent_address():
    class Silent:
        timeout = None

        def query(self, command):
            raise IOError("timeout")

        def close(self):
            pass

    class Mixed(FakeRM):
        def __init__(self):
            FakeRM.__init__(self, None)

        def open_resource(self, resource):
            return FakeCryocon() if "12" in resource else Silent()

    found = sensing.identify_resources(
        Mixed(), ["GPIB1::9::INSTR", "GPIB1::12::INSTR"])
    assert found == {"GPIB1::12::INSTR": CRYOCON_IDN}


# ------------------------------------------------------- command audit

# Every Cryo-con mnemonic either module is allowed to put on the bus, taken
# from the Remote Operation chapter of the Cryo-con User's Guide. The point of
# the list is that a mnemonic cannot be invented later without this test
# failing: adding one here is a deliberate act that should come with a manual
# reference, not something that slips in with a feature.
ALLOWED_CRYOCON_COMMANDS = {
    # IEEE-488.2 common set
    "*IDN?", "*CLS", "*RST",
    # Control-loop engage / disengage
    "CONTROL", "CONTROL?", "STOP",
    # Loop subsystem
    "LOOP:PGAIN", "LOOP:PGAIN?", "LOOP:IGAIN", "LOOP:IGAIN?",
    "LOOP:DGAIN", "LOOP:DGAIN?",
    "LOOP:SETPT", "LOOP:SETPT?", "LOOP:RATE", "LOOP:RATE?", "LOOP:RAMP?",
    "LOOP:TYPE", "LOOP:TYPE?", "LOOP:SOURCE", "LOOP:SOURCE?",
    "LOOP:RANGE", "LOOP:RANGE?", "LOOP:LOAD", "LOOP:LOAD?",
    "LOOP:MAXPWR", "LOOP:MAXPWR?", "LOOP:MAXSET", "LOOP:MAXSET?",
    "LOOP:PMANUAL", "LOOP:PMANUAL?", "LOOP:OUTPWR?", "LOOP:HTRREAD?",
    # Input subsystem
    "INPUT?", "INPUT:UNITS", "INPUT:UNITS?", "INPUT:SENPR?",
    "INPUT:ISENIX", "INPUT:ISENIX?", "INPUT:USENIX", "INPUT:USENIX?",
    "INPUT:NAME", "INPUT:NAME?", "INPUT:ALARM?",
    "INPUT:ALARM:HIGHEST", "INPUT:ALARM:HIGHEST?",
    "INPUT:ALARM:LOWEST", "INPUT:ALARM:LOWEST?",
    "INPUT:ALARM:HIENA", "INPUT:ALARM:HIENA?",
    "INPUT:ALARM:LOENA", "INPUT:ALARM:LOENA?",
    # Overtemp protection
    "OVERTEMP:ENABLE", "OVERTEMP:ENABLE?", "OVERTEMP:SOURCE",
    "OVERTEMP:SOURCE?", "OVERTEMP:TEMP", "OVERTEMP:TEMP?",
    # System subsystem
    "SYSTEM:LOCKOUT", "SYSTEM:LOCKOUT?", "SYSTEM:DRES", "SYSTEM:DRES?",
    "SYSTEM:DISTC", "SYSTEM:DISTC?", "SYSTEM:AMBIENT?", "SYSTEM:ERROR?",
    "SYSTEM:NVSAVE",

    # -- sensor curve table (Sensor_Curve_Loader / Sensor_Curve_Viewer) --
    #
    # Calibration Curve chapter of the Cryo-con User's Guide. CALCUR is the
    # curve block itself: the write form is sent line by line as raw bytes
    # and terminated with ';', and CALCUR? answers a MULTI-LINE block, which
    # is why no diagnostic sends it - one query() would read one line and
    # desynchronise the session. SENTYPE names and types a Master Sensor
    # Table slot; INPUT:SENIX points an input at one.
    "CALCUR", "CALCUR?", "INPUT:SENIX",
    "SENTYPE:NAME", "SENTYPE:NAME?", "SENTYPE:TYPE", "SENTYPE:TYPE?",

    # -- loop addressing by NAME (Diagnostics_CC34_GUI, opt-in section) --
    #
    # The open question from 17 Sep 2026: every LOOP <n>:...? and CONTROL?
    # times out, while PIDTABLE 1:NENTRY? and HEATER:AUTOTUNE:STATUS? - the
    # same shape - answer. The hypothesis is that this firmware addresses
    # loops by NAME, HEATER being loop 1 and AOUT loop 2, which is how the
    # manual's own autotune and AOUT:MODE examples are written.
    #
    # All queries, all in the opt-in "Loop addressing" probe table. They
    # are here so the hypothesis can be TESTED against the instrument; none
    # is used by a measurement module and none may be until it answers.
    "HEATER:HTRREAD?", "HEATER:OUTPWR?", "HEATER:PMANUAL?",
    "HEATER:MAXPWR?", "HEATER:MAXSET?", "HEATER:TABLEIX?", "HEATER:NAME?",
    "HEATER:SETPT?", "HEATER:SOURCE?", "HEATER:TYPE?", "HEATER:RANGE?",
    "HEATER:LOAD?", "HEATER:RATE?", "HEATER:RAMP?",
    "HEATER:PGAIN?", "HEATER:IGAIN?", "HEATER:DGAIN?",
    "HEATER:AUTOTUNE:STATUS?", "HEATER:AUTOTUNE:DELTAP?",
    "AOUT:SETPT?", "AOUT:TYPE?", "AOUT:MODE?", "AOUT:HTRREAD?",

    # -- deliberately WRONG spellings (Diagnostics_CC34_GUI) --
    #
    # Sent on purpose, to prove they are refused. The 17 Sep runs showed
    # the parser is whitespace- and form-fussy ('INPUT? A' works, 'INPUT ? A'
    # and 'INPUT?A' do not), and that several documented commands time out.
    # These near-misses ask whether a short form is what the firmware wants.
    # A measurement module using one of these would be a bug; that is caught
    # by test_the_passive_module_uses_only_query_mnemonics and by the fact
    # that they appear only in the diagnostics probe tables.
    "INPUT:SENP?", "LOOP:SETP?", "SYSTEM:HOME?", "SYSTEM:LINEF?",
    "SYSTEM:LINEFREQ?", "PIDTABLE?", "PIDTABLE:NENTRY?", "RELAYS?",
    "STATS:TIME?",

    # -- read-only self-test survey (direct-control module, 17 Sep 2026) --
    #
    # The survey exists BECAUSE this list was taken from the Model 32/32B
    # manual: six of the mnemonics above (LOOP:OUTPWR?, LOOP:MAXPWR?,
    # LOOP:MAXSET?, INPUT:SENPR?, INPUT:ISENIX?, INPUT:USENIX?) are in no
    # Model 34 manual, and a Cryo-con answers a command it does not know
    # with silence rather than an error. The survey asks the instrument
    # which ones it really takes.
    #
    # Every mnemonic below is a QUERY and appears only in the survey's
    # probe tables. Adding a WRITE here would still be a deliberate act -
    # and test_cryocon_self_test.py separately refuses any write path in
    # the survey at all. Manual references are on each probe in the source.
    "*OPC?", "*ESR?", "*ESE?", "*STB?",
    "INPUT:TEMPER?", "INP?", "INP:TEMP?", "INP:UNIT?",
    "INPUT:SENIX?", "INPUT:BIAS?", "INPUT:ALARM:FAULT?",
    "INPUT:MINIMUM?", "INPUT:MAXIMUM?", "INPUT:VARIANCE?", "INPUT:SLOPE?",
    "LOOP:HTRR?", "LOOP:TABLEIX?", "LOOP:NAME?",
    "SENTYPE?", "SENTYPE:TYPE?", "SENTYPE:MULTIPLY?",
    "PIDTABLE?", "PIDTABLE:NENTRY?",
    "HEATER:AUTOTUNE:STATUS?", "HEATER:AUTOTUNE:DELTAP?",
    "SYSTEM:LOOP?", "SYSTEM:HWREV?", "SYSTEM:FWREV?", "SYSTEM:NAME?",
    "SYSTEM:ADRS?", "SYSTEM:REMOTE?", "SYSTEM:HTRHST?", "SYSTEM:CJTEMP?",
    "SYSTEM:LINEFREQ?", "SYSTEM:REMLED?", "SYSTEM:CONTRAST?",
    "RELAYS?", "STATS:TIME?",
}


def _mnemonics(command):
    """Reduce one command string to its bare mnemonics.

    'LOOP 1:PGAIN 10;IGAIN 20' -> ['LOOP:PGAIN', 'LOOP:IGAIN'].
    Channel letters, loop numbers and values are stripped, and a semicolon
    continues the path minus its last node, exactly as SCPI defines it.
    """
    out = []
    prefix = []
    for clause in command.split(";"):
        nodes = [node.strip() for node in clause.strip().split(":") if node.strip()]
        if not nodes:
            continue
        # Drop the argument from each node: "LOOP 1" -> "LOOP", "SETPT 300"
        # -> "SETPT", 'NAME "x"' -> "NAME".
        cleaned = [node.split()[0] if node.split() else node for node in nodes]
        # A trailing '?' belongs to the last node.
        if clause.strip().endswith("?") and not cleaned[-1].endswith("?"):
            cleaned[-1] += "?"
        if len(cleaned) > 1 or not prefix:
            prefix = cleaned[:-1]
            out.append(":".join(cleaned))
        else:
            out.append(":".join(prefix + cleaned))
    return out


def _all_commands(source):
    """Every command string either module can put on the bus."""
    import ast
    commands = list(_command_literals(source))
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(t, "id", None) == "cmd" for t in node.targets):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            commands.append(value.value)
        elif isinstance(value, ast.JoinedStr):
            commands.append("".join(
                part.value if isinstance(part, ast.Constant) else "X"
                for part in value.values))
    return commands


def test_no_cryocon_command_outside_the_vetted_mnemonic_list():
    """Guards against a mnemonic being invented rather than transcribed."""
    for name, source in (("T_Sensing_CC34_GUI.py", SENSING_SOURCE),
                         ("T_Control_CC34_DirectControl_GUI.py", CONTROL_SOURCE)):
        for command in _all_commands(source):
            for mnemonic in _mnemonics(command):
                assert mnemonic in ALLOWED_CRYOCON_COMMANDS,                     f"{name}: {command!r} uses unvetted {mnemonic!r}"


def test_the_mnemonic_reducer_follows_the_scpi_semicolon_rule():
    """A semicolon keeps the path minus its last node."""
    assert _mnemonics("LOOP 1:PGAIN 10;IGAIN 20;DGAIN 0") ==         ["LOOP:PGAIN", "LOOP:IGAIN", "LOOP:DGAIN"]
    assert _mnemonics("INPUT A:UNITS?") == ["INPUT:UNITS?"]
    assert _mnemonics("INPUT? A") == ["INPUT?"]
    assert _mnemonics("CONTROL") == ["CONTROL"]
    assert _mnemonics("LOOP 1:RATE 0.5;TYPE RampP;SETPT 100") ==         ["LOOP:RATE", "LOOP:TYPE", "LOOP:SETPT"]


def test_the_audit_would_actually_catch_an_invented_command():
    """A test that cannot fail is not a test."""
    for mnemonic in _mnemonics("LOOP 1:TURBO 9"):
        assert mnemonic not in ALLOWED_CRYOCON_COMMANDS


# Every subsystem root a Cryo-con owns. A mnemonic whose root is not one of
# these belongs to the other instrument in a combined module (a Keithley, an
# E4980A, an SR830) and is none of this audit's business.
#
# Matched CASE-SENSITIVELY, and that is deliberate. Every Cryo-con command in
# PICA is written fully upper case; the SCPI short-form capitalisation
# 'SYSTem:ZCHeck' is a Keithley 6517B idiom. Without the case rule the shared
# SYSTEM root would drag three Keithley zero-check commands into a Cryo-con
# audit. test_the_root_filter_tells_the_two_system_subsystems_apart pins this.
CRYOCON_SUBSYSTEM_ROOTS = (
    "AOUT", "CALCUR", "CONTROL", "HEATER", "INPUT", "LOOP", "OVERTEMP",
    "PIDTABLE", "RELAYS", "SENTYPE", "STATS", "STOP", "SYSTEM",
)


def _is_cryocon_mnemonic(mnemonic):
    if "{" in mnemonic:
        return False            # an f-string placeholder, not a mnemonic
    root = mnemonic.split(":")[0].rstrip("?")
    if root not in CRYOCON_SUBSYSTEM_ROOTS:
        return False
    # A bare root with no sub-mnemonic and no '?' is a reducer artefact off
    # a prose string, not something anyone can put on a bus. CONTROL and
    # STOP are the two real bare commands.
    if ":" not in mnemonic and not mnemonic.endswith("?"):
        return mnemonic in ("CONTROL", "STOP")
    return True


# Only strings SHAPED like a command are audited. Without this, prose such
# as "CONTROL, loop, heater or setpoint command exists" would be read as a
# CONTROL command, and a log line as a dozen of them.
_COMMAND_SHAPE = re.compile(
    r'^\*?[A-Z][A-Z0-9]*[?]?(?:[ :][A-Za-z0-9_?:*{} -]*)?$')


def _looks_like_a_cryocon_command(text):
    text = text.strip()
    if not text or len(text) > 48 or "," in text or "." in text:
        return False
    root = re.split(r'[ :;]', text, 1)[0].rstrip("?")
    return root in CRYOCON_SUBSYSTEM_ROOTS and bool(_COMMAND_SHAPE.match(text))


def _string_value(node):
    """The literal text of a str constant or an f-string, placeholders
    collapsed to '{}' so 'INPUT? {channel}' still reduces to 'INPUT?'."""
    import ast
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) and
                       isinstance(v.value, str) else "{}"
                       for v in node.values)
    return None


def _cryocon_commands_in(source):
    """Every Cryo-con command string a module can put on the bus.

    Deliberately wider than _all_commands(), which only reads literals
    passed straight to write()/query()/_write()/_query() or assigned to a
    variable called 'cmd'. That misses three shapes this repo uses
    constantly, and LOOP:OUTPWR? hid in the first of them:

      * a module-level constant sent later - CRYOCON_STOP_COMMAND = "STOP"
        then link.write(CRYOCON_STOP_COMMAND)
      * a literal handed to a differently named helper - self.ask(...),
        probe_heater_command(...), _safe_query(...)
      * a probe TABLE - ("LOOP 1:HTRREAD?", "why we ask") in a list of
        tuples, which is how both diagnostic surveys hold their commands

    So: resolve simple constants, accept ANY callee, and read the first
    element of every tuple. The shape filter above keeps prose out.
    """
    import ast
    tree = ast.parse(source)
    constants = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        text = _string_value(node.value)
        if text is None:
            continue
        for target in node.targets:
            if getattr(target, "id", None):
                constants[target.id] = text

    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            args = list(node.args) + [kw.value for kw in node.keywords]
            for arg in args:
                text = _string_value(arg)
                if text is None and isinstance(arg, ast.Name):
                    text = constants.get(arg.id)
                if text and _looks_like_a_cryocon_command(text):
                    found.append(text)
        elif isinstance(node, ast.Tuple) and node.elts:
            text = _string_value(node.elts[0])
            if text and _looks_like_a_cryocon_command(text):
                found.append(text)
    return found


def _every_module_that_talks_to_a_cryocon():
    """Found by the marker constant, not listed by hand.

    This is the whole point of the 17 Sep 2026 lesson. LOOP:OUTPWR? lived
    for weeks in Temprature_Scan_Passive_CC34_E4980A_GUI.py and in
    RT_K6517B_CC34_T_Sensing_GUI.py, and the audit below - the one test
    written to catch exactly that - only ever looked at the two modules in
    pica/cryocon/. It could not have found it. Discovering the modules
    means a new one is covered the day it is written, not the day someone
    remembers to add it here.
    """
    found = {}
    for folder, _dirs, names in os.walk(os.path.join(REPO_ROOT, "pica")):
        if "__pycache__" in folder:
            continue
        for name in sorted(names):
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            source = open(path, encoding="utf-8").read()
            if "CRYOCON_IDN_MARKERS" in source:
                found[os.path.relpath(path, REPO_ROOT)] = source
    return found


def test_the_cryocon_audit_covers_every_module_that_talks_to_one():
    """A guard that inspects two files out of fifteen is not a guard."""
    modules = _every_module_that_talks_to_a_cryocon()
    assert len(modules) >= 15, sorted(modules)
    flat = {path.replace("\\", "/") for path in modules}
    for expected in ("pica/cryocon/T_Sensing_CC34_GUI.py",
                     "pica/cryocon/T_Control_CC34_DirectControl_GUI.py",
                     "pica/keysight/Temprature_Scan_Passive_CC34_E4980A_GUI.py",
                     "pica/keithley/k6517b/High_Resistance/"
                     "RT_K6517B_CC34_T_Sensing_GUI.py"):
        assert expected in flat, sorted(flat)


def test_no_cryocon_command_in_ANY_module_is_outside_the_vetted_list():
    """The same audit as above, over every module that talks to a Cryo-con.

    A Cryo-con answers a mnemonic it does not recognise with silence, so a
    wrong one cannot be caught at the call site - it looks exactly like a
    dead bus. It has to be caught here.
    """
    offenders = []
    for path, source in sorted(_every_module_that_talks_to_a_cryocon().items()):
        for command in _cryocon_commands_in(source):
            for mnemonic in _mnemonics(command):
                if not _is_cryocon_mnemonic(mnemonic):
                    continue
                if mnemonic not in ALLOWED_CRYOCON_COMMANDS:
                    offenders.append(f"{path}: {command!r} uses "
                                     f"unvetted {mnemonic!r}")
    assert not offenders, chr(10).join(offenders)


def test_the_root_filter_tells_the_two_system_subsystems_apart():
    """RT_K6517B_CC34_T_Sensing_GUI.py drives a Keithley 6517B AND a
    Cryo-con. Both have a SYSTEM subsystem. The Keithley's zero-check
    commands must not be audited as Cryo-con mnemonics, and the Cryo-con's
    must not be let through as Keithley ones."""
    for keithley in ("SYSTem:ZCHeck", "SYSTem:ZCORrect",
                     "SYSTem:ZCORrect:ACQuire"):
        assert not _is_cryocon_mnemonic(keithley), keithley
    for cryocon in ("SYSTEM:DRES?", "SYSTEM:ERROR?", "INPUT?", "LOOP:SETPT",
                    "CALCUR?", "SENTYPE:NAME"):
        assert _is_cryocon_mnemonic(cryocon), cryocon


def test_the_cross_module_audit_would_catch_the_bug_that_started_all_this():
    """LOOP:OUTPWR? is still vetted - the decision was probe-once-then-fall-
    back, so a Model 32/32B keeps the feature - but an INVENTED mnemonic in
    one of the thirteen modules the old audit never read must fail."""
    for mnemonic in _mnemonics("LOOP 1:TURBO 9"):
        assert _is_cryocon_mnemonic(mnemonic)
        assert mnemonic not in ALLOWED_CRYOCON_COMMANDS


def test_the_passive_module_uses_only_query_mnemonics():
    for command in _all_commands(SENSING_SOURCE):
        for mnemonic in _mnemonics(command):
            assert mnemonic.endswith("?"),                 f"passive monitor uses the non-query {mnemonic!r}"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL  {name}: {exc}")
    print(f"\n{failures} failure(s)")
    sys.exit(1 if failures else 0)
