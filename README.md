# LED Sports Scoreboard

A Raspberry Pi LED sports scoreboard for a 64×32 RGB matrix panel. It follows your favorite
teams across the NHL, MLB, NFL and NBA — live scores with instant celebrations — and fills the
time between games with last/next games, standings, fantasy football, birthdays and your own
photos.

## Hardware
- Raspberry Pi (1 GB) with an Adafruit RGB Matrix Bonnet (solder the GPIO4–GPIO18 jumper for
  `adafruit-hat-pwm`), a 64×32 4 mm RGB LED panel, a 5 V supply into the bonnet, and a 16 mm
  momentary pushbutton on GPIO3 / GND.

## Running
- `python -m ballin` — the scoreboard (render loop plus background data workers).
- `python -m ballin.web` — the phone-friendly settings page on port 80.
- Open the settings page at `http://scoreboard.local` or the board's IP, on the same network.

## Layout
- `ballin/` — the application: render loop, ESPN data, slides, live game mode, celebrations,
  fantasy football, smart lights, photos, settings and the web page.
- `assets/` — fonts, cached logos, and the settings and photo web pages.
- `tools/` — deploy, backup, preview and test utilities.
- `setup/` — build a working card from scratch, and the read-only filesystem.
