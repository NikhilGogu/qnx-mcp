# SPDX-License-Identifier: Apache-2.0
add_library(net STATIC src/udp.c src/addr.c)
target_include_directories(net PUBLIC include)
target_link_libraries(net PUBLIC core)
if(CMAKE_SYSTEM_NAME STREQUAL "QNX")
  target_link_libraries(net PUBLIC socket)
endif()
