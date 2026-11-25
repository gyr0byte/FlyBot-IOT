// Setup portal (plan step 6.12): if the robot cannot join its Wi-Fi (or both buttons are held at
// power-on, or the console says "portal"), it opens its own hotspot "FlyBot-Setup-xxxx". A phone that
// joins it is sent to a page (captive portal) to pick a network, enter its password and set the
// broker address, port and TLS. Saved to flash; the robot restarts with the new settings.
// Motors stay off while the portal is open.
#pragma once

namespace portal {
void start();      // switches Wi-Fi to access point + station and starts the web page
bool active();
void loop();       // call every few ms while active (from the comms task)
}  // namespace portal
