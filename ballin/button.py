"""The front button (GPIO3, wired to the bonnet's SCL pad and GND; pressed = low).

  short press           screen off / on ("SLEEPING")
  double press          flip the NFL mini field (match the TV)
  triple press          WiFi setup hotspot ("Ballin Board Setup"); saved networks are kept;
                        a single press while it shows cancels and returns to the scoreboard
  held while plugging in, through a 5 s countdown: factory reset (settings + saved WiFi;
                        keeps the alert email)
  hold (1 s)            "HOLD TO SHUT DOWN" with a progress bar (let go to cancel)
  hold (3 s)            safe shutdown: "SHUTTING DOWN", sync, "SAFE TO UNPLUG", power off

GPIO3 can also wake the Pi after it has halted, so a press turns the board back on.

The button's light is optional: set "button_led_gpio" (BCM number, e.g. 2 for the SDA pad)
when it's wired. It's lit while the screen is on and blinks during shutdown.
"""
import copy
import datetime
import logging
import subprocess
import threading
import time

log = logging.getLogger(__name__)

CHIP = "/dev/gpiochip0"
BUTTON = 3
HOLD_SHOW = 1.0        # seconds held before the shutdown prompt appears
HOLD_SHUTDOWN = 3.0    # seconds held to shut down
SHOW_SHUTTING = 2.5    # seconds of "SHUTTING DOWN" before powering off
SLEEP_MSG = 2.0        # seconds of "SLEEPING / PRESS TO WAKE" before the screen goes dark
MULTI_GAP = 0.45       # quick presses within this gap count together (double, triple)
RESET_HOLD = 5.0       # held this long after startup (while plugged in holding it): factory reset


class Button(threading.Thread):
    def __init__(self, state, stop):
        super().__init__(name="Button", daemon=True)
        self.state = state
        self.stop = stop
        self.led = None

    def run(self):
        try:
            import gpiod
            from gpiod.line import Bias, Direction, Edge, Value
        except ImportError:
            log.warning("gpiod not available; button disabled")
            return
        self.Value = Value
        try:
            req = gpiod.request_lines(CHIP, consumer="ballin-button", config={BUTTON: gpiod.LineSettings(
                direction=Direction.INPUT, bias=Bias.PULL_UP, edge_detection=Edge.BOTH,
                debounce_period=datetime.timedelta(milliseconds=20))})
        except OSError as exc:
            log.error("Button unavailable: %s", exc)
            return
        self._setup_led(gpiod, Direction, Value)
        log.info("Button ready on GPIO%d", BUTTON)
        if req.get_value(BUTTON) == Value.INACTIVE:     # held while the board was plugged in
            self._factory_reset_watch(req, Value)
        pressed_at = None
        presses, last_release = 0, 0.0
        quiet_until = time.monotonic() + 1.5    # after a startup reset hold: ignore release chatter
        while not self.stop.is_set():
            self._update_led()
            if req.wait_edge_events(datetime.timedelta(milliseconds=50)):
                for ev in req.read_edge_events():
                    if time.monotonic() < quiet_until:
                        pressed_at = None        # contacts still settling after a long hold
                        continue
                    if ev.event_type == ev.Type.FALLING_EDGE:
                        pressed_at = time.monotonic()
                    elif pressed_at is not None:
                        held = time.monotonic() - pressed_at
                        pressed_at = None
                        self.state.hold_started = None
                        if held < HOLD_SHOW and not self.state.shutting_down:
                            presses += 1
                            last_release = time.monotonic()
                        else:
                            quiet_until = time.monotonic() + 0.5
            # Quick presses are counted, then acted on once the presses stop.
            if presses and pressed_at is None and time.monotonic() - last_release > MULTI_GAP:
                self._presses(presses)
                presses = 0
            if pressed_at is not None and not self.state.shutting_down:
                held = time.monotonic() - pressed_at
                if held >= HOLD_SHOW:
                    self.state.hold_started = pressed_at
                if held >= HOLD_SHUTDOWN:
                    pressed_at = None
                    self.state.hold_started = None
                    self._shutdown()

    def _presses(self, n):
        from . import netsetup
        if n == 1 and self.state.netmode.get("mode") == "setup":
            log.info("Button: cancel WiFi setup")
            netsetup.request_setup("cancel")      # back to the saved network and the scoreboard
        elif n == 1:
            self._toggle_screen()
        elif n == 2:
            self.state.flip_field = not self.state.flip_field
            log.info("Button: NFL field flipped (%s)", self.state.flip_field)
        else:
            log.info("Button: WiFi setup requested")
            netsetup.request_setup("button")
            self.state.screen_override = True

    def _factory_reset_watch(self, req, Value):
        """Button held at power-up: count down; letting go cancels, holding through resets."""
        log.info("Button held at startup: factory reset countdown")
        start = time.monotonic()
        self.state.reset_started = start
        while time.monotonic() - start < RESET_HOLD:
            if req.get_value(BUTTON) == Value.ACTIVE:     # released
                log.info("Factory reset cancelled")
                self.state.reset_started = None
                while req.wait_edge_events(datetime.timedelta(0)):
                    req.read_edge_events()
                return
            time.sleep(0.05)
        self.state.reset_started = None
        self._factory_reset()
        # Wait for the button to be let go so it isn't taken as a hold-to-shut-down.
        while req.get_value(BUTTON) == Value.INACTIVE:
            time.sleep(0.05)
        while req.wait_edge_events(datetime.timedelta(milliseconds=100)):
            req.read_edge_events()

    def _factory_reset(self):
        from . import netsetup, settings as settings_mod
        log.warning("FACTORY RESET: settings to defaults; saved WiFi will be forgotten")
        self.state.reset_done_until = time.monotonic() + 6
        settings_mod.save(copy.deepcopy(settings_mod.DEFAULTS))
        self.state.settings = settings_mod.load()
        # The network service forgets the WiFi (it waits for networking to be up) and then
        # starts the setup hotspot.
        netsetup.request_setup("reset")

    def _toggle_screen(self):
        on = not self.state.screen_visible
        if not on:
            self.state.sleep_msg_until = time.monotonic() + SLEEP_MSG
        else:
            self.state.card_since = time.monotonic()     # remind how to reach the settings page
        self.state.screen_override = on
        log.info("Button: screen %s", "on" if on else "off")

    def _shutdown(self):
        log.info("Button: shutting down")
        self.state.shutting_down = time.monotonic()
        subprocess.run(["sync"], timeout=30)
        time.sleep(max(0.0, SHOW_SHUTTING - (time.monotonic() - self.state.shutting_down)))
        self.state.safe_to_unplug = time.monotonic()      # countdown starts as power-off begins
        subprocess.run(["sync"], timeout=30)
        subprocess.Popen(["systemctl", "poweroff"])

    # ------------------------------------------------------------- optional light
    def _setup_led(self, gpiod, Direction, Value):
        pin = self.state.settings.get("button_led_gpio")
        if pin is None or pin == "":
            return
        try:
            pin = int(pin)
            self.led = gpiod.request_lines(CHIP, consumer="ballin-button-led", config={
                pin: gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.ACTIVE)})
            self.led_pin = pin
            log.info("Button light on GPIO%d", pin)
        except (OSError, ValueError) as exc:
            log.warning("Button light unavailable: %s", exc)
            self.led = None

    def _update_led(self):
        if not self.led:
            return
        if self.state.shutting_down:
            on = int(time.monotonic() * 4) % 2 == 0          # blink while shutting down
        else:
            on = self.state.screen_visible
        try:
            self.led.set_value(self.led_pin, self.Value.ACTIVE if on else self.Value.INACTIVE)
        except OSError:
            pass
