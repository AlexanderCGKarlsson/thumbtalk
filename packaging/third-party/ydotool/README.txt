ydotool source: https://github.com/ReimuNotMoe/ydotool
Revision: 57ba7d0af525e82da2de0e275d169477f293b197 (same as Decktation 32e1a7d)
License: GNU AGPL version 3; see LICENSE. Complete unmodified source is included.
Rebuild both tools with bash build-linux-input.sh /tmp/thumbtalk-input-build.
In the source checkout that script is in packaging/; in the app bundle it
is alongside this README and the complete source archive. Requires Linux cc
and standard libc/Linux kernel development headers. No source modifications.
ThumbTalk invokes these separate executables through their command-line interface.
