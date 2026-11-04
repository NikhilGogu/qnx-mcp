# SPDX-License-Identifier: Apache-2.0
add_library(core STATIC src/crc.c src/ring.c src/clock.c src/fmt.c)
target_include_directories(core PUBLIC include)
