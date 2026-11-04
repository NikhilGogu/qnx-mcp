# Per trial

| model | task | arm | rep | target | oracle | root cause | location | fix in GT region | wall s | turns | tool calls (MCP/Bash) | input tok (incl. cache) | output tok | cost USD | capped | flags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| haiku | v2-01-config-overflow | A | 1 | e7593cc | pass | True | True | True | 85.5 | 20 | 19 (0/17) | 900130 | 15714 | 0.0238 |  | 0 |
| haiku | v2-01-config-overflow | C | 1 | efbff69 | pass | True | True | True | 98.3 | 48 | 47 (35/10) | 1237949 | 17107 | 0.031 |  | 0 |
| haiku | v2-01-config-overflow | A | 2 | eec965a | pass | True | True | True | 80.5 | 15 | 14 (0/12) | 802523 | 15124 | 0.0232 |  | 0 |
| haiku | v2-01-config-overflow | C | 2 | e4635b2 | pass | True | True | True | 105.2 | 33 | 32 (18/12) | 1245793 | 17782 | 0.0301 |  | 0 |
| haiku | v2-01-config-overflow | A | 3 | eb5ae3a | pass | True | True | True | 111.8 | 24 | 23 (0/21) | 1075940 | 21725 | 0.03 |  | 0 |
| haiku | v2-01-config-overflow | C | 3 | ec944f1 | pass | True | True | True | 88.3 | 26 | 25 (12/10) | 1133201 | 14550 | 0.0271 |  | 0 |
| haiku | v2-01-config-overflow | A | 4 | e2ae46a | pass | True | True | True | 108.0 | 23 | 22 (0/16) | 851546 | 20036 | 0.0248 |  | 0 |
| haiku | v2-01-config-overflow | C | 4 | eddf0e2 | pass | True | True | True | 74.8 | 23 | 22 (13/7) | 785711 | 12524 | 0.0211 |  | 0 |
| haiku | v2-01-config-overflow | A | 5 | e81bb63 | pass | True | True | True | 97.4 | 22 | 21 (0/16) | 932320 | 17958 | 0.026 |  | 0 |
| haiku | v2-01-config-overflow | C | 5 | e61d8fa | pass | True | True | True | 123.9 | 48 | 47 (35/9) | 1208664 | 21078 | 0.032 |  | 0 |
| haiku | v2-02-mp-deadlock | A | 1 | e969620 | pass | True | True | False | 89.0 | 10 | 9 (0/8) | 365566 | 11663 | 0.0135 |  | 0 |
| haiku | v2-02-mp-deadlock | C | 1 | e942308 | pass | True | True | False | 219.0 | 64 | 63 (54/3) | 1973417 | 21027 | 0.0382 |  | 1 |
| haiku | v2-02-mp-deadlock | A | 2 | e3623ed | pass | True | True | False | 211.1 | 15 | 14 (0/13) | 509311 | 16167 | 0.0177 |  | 0 |
| haiku | v2-02-mp-deadlock | C | 2 | edd54eb | pass | True | True | False | 179.6 | 49 | 48 (41/5) | 1313021 | 20213 | 0.0304 |  | 0 |
| haiku | v2-02-mp-deadlock | A | 3 | ebd9d4a | pass | True | True | False | 99.0 | 14 | 13 (0/10) | 498959 | 11785 | 0.0147 |  | 0 |
| haiku | v2-02-mp-deadlock | C | 3 | e4d25ed | pass | None | None | False | 176.6 | 45 | 44 (34/7) | 1493350 | 18417 | 0.0316 |  | 0 |
| haiku | v2-02-mp-deadlock | A | 4 | e224ba0 | pass | True | True | False | 192.4 | 13 | 12 (0/10) | 470819 | 12330 | 0.0149 |  | 0 |
| haiku | v2-02-mp-deadlock | C | 4 | e4d9a22 | pass | True | True | False | 169.3 | 39 | 38 (30/5) | 1117914 | 16893 | 0.0265 |  | 0 |
| haiku | v2-02-mp-deadlock | A | 5 | e582ad4 | pass | True | True | False | 332.1 | 17 | 16 (0/14) | 649659 | 15734 | 0.0189 |  | 0 |
| haiku | v2-02-mp-deadlock | C | 5 | ef291f1 | pass | True | True | False | 172.9 | 42 | 41 (33/6) | 1151601 | 16589 | 0.0267 |  | 0 |
| haiku | v2-03-resmgr-slog2 | A | 1 | e92873c | pass | True | True | True | 269.2 | 16 | 15 (0/13) | 637292 | 16887 | 0.02 |  | 0 |
| haiku | v2-03-resmgr-slog2 | C | 1 | e7baaad | pass | True | True | True | 81.0 | 32 | 31 (22/6) | 937130 | 13539 | 0.0228 |  | 0 |
| haiku | v2-03-resmgr-slog2 | A | 2 | e87bdde | pass | True | True | True | 70.3 | 18 | 17 (0/16) | 501980 | 12325 | 0.0155 |  | 0 |
| haiku | v2-03-resmgr-slog2 | C | 2 | e459b2e | pass | True | True | True | 88.0 | 32 | 31 (23/6) | 848489 | 15763 | 0.0232 |  | 0 |
| haiku | v2-03-resmgr-slog2 | A | 3 | e6c7c23 | pass | True | True | True | 62.3 | 13 | 12 (0/11) | 326427 | 10493 | 0.0126 |  | 0 |
| haiku | v2-03-resmgr-slog2 | C | 3 | e98bad9 | pass | True | True | True | 109.8 | 42 | 41 (26/13) | 1003383 | 20368 | 0.0277 |  | 0 |
| haiku | v2-03-resmgr-slog2 | A | 4 | e214686 | pass | True | True | True | 206.0 | 17 | 16 (0/13) | 628119 | 13894 | 0.0182 |  | 0 |
| haiku | v2-03-resmgr-slog2 | C | 4 | e0ffac9 | pass | True | True | True | 127.1 | 43 | 42 (29/9) | 1639125 | 21946 | 0.0356 |  | 0 |
| haiku | v2-03-resmgr-slog2 | A | 5 | e57cd02 | pass | True | True | True | 111.1 | 19 | 18 (0/17) | 647379 | 17657 | 0.0209 |  | 1 |
| haiku | v2-03-resmgr-slog2 | C | 5 | e4f7a42 | pass | True | True | True | 87.0 | 32 | 31 (22/7) | 1062129 | 13711 | 0.024 |  | 0 |
| haiku | v2-04-deep-expr | A | 1 | e681484 | pass | True | True | False | 156.4 | 22 | 21 (0/18) | 867124 | 22594 | 0.0268 |  | 1 |
| haiku | v2-04-deep-expr | C | 1 | e6171db | pass | True | True | True | 91.4 | 23 | 22 (10/9) | 1041010 | 12152 | 0.0251 |  | 0 |
| haiku | v2-04-deep-expr | A | 2 | ee91aed | pass | True | True | True | 127.4 | 23 | 22 (0/18) | 1200698 | 22170 | 0.03 |  | 0 |
| haiku | v2-04-deep-expr | C | 2 | ec45d16 | pass | True | True | True | 100.1 | 40 | 39 (24/11) | 1254205 | 17013 | 0.0506 |  | 0 |
| haiku | v2-04-deep-expr | A | 3 | ef019bd | pass | True | True | True | 103.1 | 18 | 17 (0/12) | 752858 | 16929 | 0.0223 |  | 0 |
| haiku | v2-04-deep-expr | C | 3 | ee49500 | pass | True | True | True | 131.2 | 36 | 35 (23/10) | 1185122 | 16080 | 0.029 |  | 0 |
| haiku | v2-04-deep-expr | A | 4 | e78d387 | pass | True | True | True | 68.1 | 15 | 14 (0/13) | 684002 | 11048 | 0.0182 |  | 0 |
| haiku | v2-04-deep-expr | C | 4 | e61df8c | pass | True | True | True | 118.2 | 41 | 40 (23/14) | 1495477 | 18054 | 0.0334 |  | 0 |
| haiku | v2-04-deep-expr | A | 5 | eb0c77e | pass | True | True | True | 71.5 | 13 | 12 (0/11) | 574145 | 12311 | 0.0173 |  | 0 |
| haiku | v2-04-deep-expr | C | 5 | ef8df0c | pass | True | True | True | 142.5 | 48 | 47 (30/14) | 1959222 | 22583 | 0.0403 |  | 0 |
| haiku | v2-05-hang-states | A | 1 | e01df90 | pass | True | True | False | 265.3 | 22 | 21 (0/16) | 729753 | 13884 | 0.0185 |  | 0 |
| haiku | v2-05-hang-states | C | 1 | e53236e | pass | True | True | False | 121.7 | 32 | 31 (20/8) | 925597 | 17325 | 0.0245 |  | 0 |
| haiku | v2-05-hang-states | A | 2 | ed75b42 | pass | True | True | True | 254.8 | 14 | 13 (0/12) | 538692 | 21582 | 0.0211 |  | 0 |
| haiku | v2-05-hang-states | C | 2 | e84aa77 | pass | True | True | True | 114.4 | 28 | 27 (17/7) | 973989 | 15018 | 0.0244 |  | 0 |
| haiku | v2-05-hang-states | A | 3 | ead4d5a | pass | True | True | True | 84.3 | 11 | 10 (0/9) | 353963 | 12977 | 0.0152 |  | 0 |
| haiku | v2-05-hang-states | C | 3 | e59fc30 | pass | True | True | True | 112.3 | 37 | 36 (22/7) | 1205942 | 17558 | 0.0275 |  | 0 |
| haiku | v2-05-hang-states | A | 4 | e0f5b6d | pass | True | True | False | 124.7 | 14 | 13 (0/12) | 545277 | 17278 | 0.0187 |  | 0 |
| haiku | v2-05-hang-states | C | 4 | e910668 | pass | True | True | True | 121.8 | 35 | 34 (19/7) | 971823 | 17954 | 0.0278 |  | 0 |
| haiku | v2-05-hang-states | A | 5 | e90f8a7 | pass | True | True | True | 212.8 | 14 | 13 (0/12) | 536178 | 16251 | 0.0179 |  | 0 |
| haiku | v2-05-hang-states | C | 5 | e1cfc80 | pass | True | True | True | 159.2 | 35 | 34 (21/9) | 1044734 | 23853 | 0.0323 |  | 0 |
| haiku | v2-06-worker-null | A | 1 | e3f1320 | pass | True | True | True | 60.5 | 15 | 14 (0/13) | 650759 | 10753 | 0.0176 |  | 0 |
| haiku | v2-06-worker-null | C | 1 | e4f29ee | pass | True | True | True | 67.6 | 20 | 19 (8/9) | 826271 | 10711 | 0.0209 |  | 0 |
| haiku | v2-06-worker-null | A | 2 | ea7b65d | pass | True | True | True | 103.0 | 23 | 22 (0/17) | 1103170 | 17718 | 0.0263 |  | 0 |
| haiku | v2-06-worker-null | C | 2 | e7e8929 | pass | True | True | True | 66.5 | 32 | 31 (20/8) | 828976 | 11118 | 0.0215 |  | 0 |
| haiku | v2-06-worker-null | A | 3 | eef6abf | pass | True | True | True | 87.0 | 21 | 20 (0/19) | 827499 | 16181 | 0.0239 |  | 0 |
| haiku | v2-06-worker-null | C | 3 | e0922ff | pass | True | True | True | 70.2 | 27 | 26 (17/7) | 987858 | 11415 | 0.0228 |  | 0 |
| haiku | v2-06-worker-null | A | 4 | e00ff1b | pass | True | True | True | 69.1 | 16 | 15 (0/12) | 643855 | 12635 | 0.0186 |  | 0 |
| haiku | v2-06-worker-null | C | 4 | e4b2c73 | pass | True | True | True | 114.8 | 45 | 44 (31/10) | 1348536 | 19386 | 0.0399 |  | 0 |
| haiku | v2-06-worker-null | A | 5 | ef7b821 | pass | True | True | True | 68.2 | 16 | 15 (0/14) | 625251 | 11544 | 0.0177 |  | 0 |
| haiku | v2-06-worker-null | C | 5 | ea83ffc | pass | True | True | True | 72.9 | 35 | 34 (20/4) | 784158 | 12032 | 0.0209 |  | 0 |
| haiku | v2-07-cmake-cross | A | 1 | eebfcb1 | pass | True | True | True | 153.5 | 33 | 32 (0/24) | 1457283 | 27787 | 0.0353 |  | 0 |
| haiku | v2-07-cmake-cross | C | 1 | e692d4b | pass | True | True | True | 176.2 | 49 | 47 (11/27) | 2204274 | 31057 | 0.0462 |  | 0 |
| haiku | v2-07-cmake-cross | A | 2 | e1855cc | pass | True | True | True | 125.9 | 31 | 30 (0/23) | 1055146 | 22592 | 0.0275 |  | 1 |
| haiku | v2-07-cmake-cross | C | 2 | efaa2ff | pass | True | True | True | 163.6 | 40 | 39 (5/25) | 1825116 | 28411 | 0.0413 |  | 0 |
| haiku | v2-07-cmake-cross | A | 3 | e3fdec3 | pass | True | True | True | 147.9 | 42 | 41 (0/30) | 1569882 | 27307 | 0.0364 |  | 4 |
| haiku | v2-07-cmake-cross | C | 3 | e13210e | pass | True | True | True | 127.1 | 37 | 36 (6/26) | 1639427 | 21500 | 0.0348 |  | 0 |
| haiku | v2-07-cmake-cross | A | 4 | ede9b2d | pass | True | True | True | 165.5 | 44 | 43 (0/33) | 1578733 | 29484 | 0.0381 |  | 0 |
| haiku | v2-07-cmake-cross | C | 4 | e903aaa | pass | True | True | True | 137.6 | 47 | 46 (10/28) | 1843590 | 23235 | 0.0384 |  | 0 |
| haiku | v2-07-cmake-cross | A | 5 | e403980 | pass | True | True | True | 153.6 | 42 | 41 (0/31) | 1388117 | 25026 | 0.0327 |  | 0 |
| haiku | v2-07-cmake-cross | C | 5 | e71cc86 | pass | True | True | True | 128.7 | 43 | 42 (8/24) | 1410446 | 23088 | 0.0336 |  | 0 |
| haiku | v2-08-fd-leak | A | 1 | e2d7051 | pass | True | True | True | 71.8 | 12 | 11 (0/10) | 407752 | 11119 | 0.0133 |  | 0 |
| haiku | v2-08-fd-leak | C | 1 | e6ef05d | pass | True | True | True | 103.0 | 33 | 32 (22/7) | 1064686 | 17108 | 0.0262 |  | 0 |
| haiku | v2-08-fd-leak | A | 2 | e2a8ac5 | pass | True | True | True | 77.0 | 13 | 12 (0/11) | 459319 | 11682 | 0.0141 |  | 0 |
| haiku | v2-08-fd-leak | C | 2 | ed6ecf4 | pass | True | True | True | 131.9 | 47 | 46 (35/7) | 1355360 | 22643 | 0.0329 |  | 0 |
| haiku | v2-08-fd-leak | A | 3 | ebc392a | pass | True | True | True | 83.3 | 12 | 11 (0/10) | 419437 | 14121 | 0.0152 |  | 0 |
| haiku | v2-08-fd-leak | C | 3 | e1a50db | pass | True | True | True | 86.1 | 33 | 32 (23/6) | 1032468 | 14101 | 0.0239 |  | 0 |
| haiku | v2-08-fd-leak | A | 4 | e1024e3 | pass | True | True | True | 75.7 | 17 | 16 (0/14) | 603516 | 11893 | 0.0157 |  | 1 |
| haiku | v2-08-fd-leak | C | 4 | e25b2bd | pass | True | True | True | 115.1 | 42 | 41 (30/8) | 1194045 | 18525 | 0.0281 |  | 0 |
| haiku | v2-08-fd-leak | A | 5 | e563fc2 | pass | True | True | True | 82.6 | 12 | 11 (0/10) | 427736 | 13417 | 0.0149 |  | 0 |
| haiku | v2-08-fd-leak | C | 5 | ebdc129 | pass | True | True | True | 89.1 | 29 | 28 (15/9) | 985449 | 14714 | 0.0242 |  | 0 |
| sonnet | v2-01-config-overflow | A | 1 | eec1d19 | pass | True | True | True | 34.0 | 9 | 8 (0/6) | 251043 | 3107 | 0.1267 |  | 0 |
| sonnet | v2-01-config-overflow | C | 1 | e087a2e | pass | True | True | True | 46.0 | 18 | 17 (9/4) | 471223 | 4024 | 0.1977 |  | 0 |
| sonnet | v2-01-config-overflow | A | 2 | eefa7ee | pass | True | True | True | 37.6 | 13 | 12 (0/10) | 311093 | 3646 | 0.1339 |  | 0 |
| sonnet | v2-01-config-overflow | C | 2 | ed5120c | pass | True | True | True | 42.6 | 18 | 17 (10/5) | 438791 | 3594 | 0.1885 |  | 0 |
| sonnet | v2-01-config-overflow | A | 3 | e947696 | pass | True | True | True | 35.2 | 10 | 9 (0/8) | 260720 | 3138 | 0.1166 |  | 0 |
| sonnet | v2-01-config-overflow | C | 3 | e84e361 | pass | True | True | True | 44.2 | 20 | 19 (10/6) | 483573 | 3955 | 0.1892 |  | 0 |
| sonnet | v2-01-config-overflow | A | 4 | e79bd5e | pass | True | True | True | 42.3 | 12 | 11 (0/9) | 309205 | 3880 | 0.1364 |  | 0 |
| sonnet | v2-01-config-overflow | C | 4 | e030b80 | pass | True | True | True | 48.4 | 19 | 18 (9/6) | 513795 | 4009 | 0.1977 |  | 0 |
| sonnet | v2-01-config-overflow | A | 5 | e9886e5 | pass | True | True | True | 28.9 | 11 | 10 (0/8) | 261510 | 3074 | 0.1163 |  | 0 |
| sonnet | v2-01-config-overflow | C | 5 | e38c116 | pass | True | True | True | 37.5 | 17 | 16 (8/3) | 447285 | 3352 | 0.1781 |  | 0 |
| sonnet | v2-02-mp-deadlock | A | 1 | ea33c3d | pass | True | True | False | 41.7 | 11 | 10 (0/8) | 240366 | 3630 | 0.1176 |  | 0 |
| sonnet | v2-02-mp-deadlock | C | 1 | e9caaba | pass | True | True | False | 127.4 | 29 | 28 (18/8) | 725550 | 6419 | 0.2506 |  | 0 |
| sonnet | v2-02-mp-deadlock | A | 2 | e260bac | pass | True | True | False | 36.2 | 10 | 9 (0/7) | 197370 | 3538 | 0.1081 |  | 0 |
| sonnet | v2-02-mp-deadlock | C | 2 | e232527 | pass | True | True | False | 129.2 | 31 | 30 (22/6) | 833006 | 6526 | 0.2658 |  | 0 |
| sonnet | v2-02-mp-deadlock | A | 3 | eaa105c | pass | True | True | False | 38.6 | 13 | 12 (0/10) | 288208 | 3829 | 0.1217 |  | 0 |
| sonnet | v2-02-mp-deadlock | C | 3 | ea044a1 | pass | True | True | False | 143.6 | 35 | 34 (24/6) | 829223 | 7110 | 0.2622 |  | 0 |
| sonnet | v2-02-mp-deadlock | A | 4 | e6f0c16 | pass | True | True | False | 38.3 | 11 | 10 (0/8) | 229083 | 3754 | 0.1142 |  | 0 |
| sonnet | v2-02-mp-deadlock | C | 4 | ebf6097 | pass | True | True | False | 143.4 | 43 | 42 (31/7) | 1055998 | 8726 | 0.3538 |  | 0 |
| sonnet | v2-02-mp-deadlock | A | 5 | e000878 | pass | True | True | False | 29.3 | 10 | 9 (0/7) | 193690 | 2972 | 0.1014 |  | 0 |
| sonnet | v2-02-mp-deadlock | C | 5 | e80960e | pass | True | True | False | 132.0 | 33 | 32 (23/6) | 930314 | 7353 | 0.2818 |  | 0 |
| sonnet | v2-03-resmgr-slog2 | A | 1 | e28034a | pass | True | True | True | 746.3 | 2 | 8 (0/6) | 78981 | 462 | 0.1936 |  | 0 |
| sonnet | v2-03-resmgr-slog2 | C | 1 | e8e89af | pass | True | True | True | 46.8 | 24 | 23 (15/4) | 564131 | 4728 | 0.202 |  | 0 |
| sonnet | v2-03-resmgr-slog2 | A | 2 | e58177a | pass | True | True | True | 34.2 | 10 | 9 (0/7) | 224111 | 3375 | 0.1465 |  | 0 |
| sonnet | v2-03-resmgr-slog2 | C | 2 | e465d95 | pass | True | True | True | 58.2 | 29 | 28 (16/7) | 821408 | 5801 | 0.2553 |  | 0 |
| sonnet | v2-03-resmgr-slog2 | A | 3 | e592beb | pass | True | True | True | 40.7 | 10 | 9 (0/7) | 225102 | 3140 | 0.1183 |  | 1 |
| sonnet | v2-03-resmgr-slog2 | C | 3 | e43e7ac | pass | True | True | True | 44.3 | 19 | 18 (12/2) | 371240 | 4501 | 0.176 |  | 0 |
| sonnet | v2-03-resmgr-slog2 | A | 4 | eecbd9f | pass | True | True | True | 171.9 | 11 | 10 (0/9) | 286411 | 3838 | 0.1343 |  | 0 |
| sonnet | v2-03-resmgr-slog2 | C | 4 | e64d7d1 | pass | True | True | True | 42.8 | 19 | 18 (11/2) | 311031 | 3819 | 0.1595 |  | 0 |
| sonnet | v2-03-resmgr-slog2 | A | 5 | ee6f0f0 | pass | True | True | True | 742.0 | 2 | 9 (0/7) | 74232 | 562 | 0.1805 |  | 0 |
| sonnet | v2-03-resmgr-slog2 | C | 5 | e4eb9f8 | pass | True | True | True | 51.5 | 24 | 23 (13/5) | 598357 | 5213 | 0.2277 |  | 0 |
| sonnet | v2-04-deep-expr | A | 1 | eae83a6 | pass | False | False | False | 58.0 | 10 | 9 (0/8) | 294242 | 3860 | 0.1389 |  | 1 |
| sonnet | v2-04-deep-expr | C | 1 | e60a41e | pass | True | True | True | 35.5 | 15 | 14 (7/5) | 406852 | 3144 | 0.1708 |  | 0 |
| sonnet | v2-04-deep-expr | A | 2 | e7b3052 | pass | True | True | True | 26.0 | 8 | 7 (0/5) | 175346 | 2905 | 0.1048 |  | 0 |
| sonnet | v2-04-deep-expr | C | 2 | e4f9993 | pass | True | True | True | 39.7 | 16 | 15 (6/6) | 441657 | 3606 | 0.1797 |  | 0 |
| sonnet | v2-04-deep-expr | A | 3 | ed023de | pass | True | True | False | 65.0 | 11 | 10 (0/8) | 280783 | 4013 | 0.1332 |  | 0 |
| sonnet | v2-04-deep-expr | C | 3 | eea367e | pass | True | True | True | 34.4 | 13 | 12 (7/2) | 348144 | 2636 | 0.1553 |  | 0 |
| sonnet | v2-04-deep-expr | A | 4 | e2a9971 | pass | True | True | True | 28.1 | 10 | 9 (0/7) | 207552 | 3076 | 0.1102 |  | 0 |
| sonnet | v2-04-deep-expr | C | 4 | edff5c9 | pass | True | True | True | 32.8 | 14 | 13 (7/3) | 390786 | 2753 | 0.1619 |  | 0 |
| sonnet | v2-04-deep-expr | A | 5 | e33efff | pass | True | True | True | 32.1 | 10 | 9 (0/7) | 244263 | 3536 | 0.1232 |  | 0 |
| sonnet | v2-04-deep-expr | C | 5 | ee69831 | pass | True | True | True | 32.9 | 14 | 13 (7/3) | 351475 | 2838 | 0.1592 |  | 0 |
| sonnet | v2-05-hang-states | A | 1 | e194eab | pass | True | True | False | 39.9 | 8 | 7 (0/5) | 162008 | 3072 | 0.0972 |  | 0 |
| sonnet | v2-05-hang-states | C | 1 | e1d526d | pass | True | True | True | 31.5 | 13 | 12 (6/3) | 239006 | 3026 | 0.1275 |  | 0 |
| sonnet | v2-05-hang-states | A | 2 | ede073a | pass | True | True | True | 44.4 | 7 | 6 (0/5) | 128312 | 2809 | 0.0874 |  | 0 |
| sonnet | v2-05-hang-states | C | 2 | efac608 | pass | True | True | False | 40.9 | 18 | 17 (7/6) | 393911 | 3827 | 0.1515 |  | 0 |
| sonnet | v2-05-hang-states | A | 3 | e60b76f | pass | True | True | False | 48.3 | 8 | 7 (0/5) | 158973 | 3024 | 0.0935 |  | 0 |
| sonnet | v2-05-hang-states | C | 3 | e67570c | pass | True | True | True | 61.6 | 22 | 21 (14/5) | 461384 | 5536 | 0.2021 |  | 0 |
| sonnet | v2-05-hang-states | A | 4 | ec1373d | pass | True | True | True | 42.2 | 8 | 7 (0/5) | 129394 | 3144 | 0.0923 |  | 0 |
| sonnet | v2-05-hang-states | C | 4 | eadab8a | pass | True | True | True | 49.7 | 15 | 14 (8/3) | 387804 | 3906 | 0.1612 |  | 0 |
| sonnet | v2-05-hang-states | A | 5 | e89bed9 | pass | True | True | True | 59.9 | 10 | 9 (0/7) | 221542 | 3925 | 0.1132 |  | 0 |
| sonnet | v2-05-hang-states | C | 5 | e161166 | pass | True | True | False | 61.6 | 23 | 22 (12/6) | 441757 | 4834 | 0.2013 |  | 0 |
| sonnet | v2-06-worker-null | A | 1 | ea1d986 | pass | True | True | True | 32.1 | 10 | 9 (0/7) | 226976 | 3149 | 0.1359 |  | 0 |
| sonnet | v2-06-worker-null | C | 1 | ef228b0 | pass | True | True | True | 52.9 | 21 | 20 (12/4) | 596296 | 3980 | 0.1986 |  | 0 |
| sonnet | v2-06-worker-null | A | 2 | e7fd5a8 | pass | True | True | True | 43.4 | 15 | 14 (0/11) | 417515 | 3901 | 0.1514 |  | 0 |
| sonnet | v2-06-worker-null | C | 2 | ec2de17 | pass | True | True | True | 35.3 | 16 | 15 (9/2) | 304912 | 2833 | 0.1497 |  | 0 |
| sonnet | v2-06-worker-null | A | 3 | ed261a8 | pass | True | True | True | 32.7 | 10 | 9 (0/8) | 281052 | 3380 | 0.1309 |  | 0 |
| sonnet | v2-06-worker-null | C | 3 | eae57eb | pass | True | True | True | 38.7 | 20 | 19 (11/2) | 463636 | 3483 | 0.1867 |  | 0 |
| sonnet | v2-06-worker-null | A | 4 | eaacc72 | pass | True | True | True | 29.7 | 9 | 8 (0/6) | 215343 | 2876 | 0.1227 |  | 0 |
| sonnet | v2-06-worker-null | C | 4 | eaffc70 | pass | True | True | True | 35.9 | 16 | 15 (8/2) | 418933 | 3126 | 0.1657 |  | 0 |
| sonnet | v2-06-worker-null | A | 5 | e2abdcc | pass | True | True | True | 33.9 | 11 | 10 (0/8) | 276178 | 3297 | 0.1285 |  | 0 |
| sonnet | v2-06-worker-null | C | 5 | e9932eb | pass | True | True | True | 40.7 | 18 | 17 (9/2) | 408123 | 3287 | 0.1775 |  | 0 |
| sonnet | v2-07-cmake-cross | A | 1 | e7cb100 | pass | True | True | True | 56.3 | 16 | 15 (0/14) | 480988 | 5195 | 0.1689 |  | 0 |
| sonnet | v2-07-cmake-cross | C | 1 | e42449b | pass | True | True | True | 66.0 | 20 | 19 (4/13) | 602074 | 5268 | 0.1876 |  | 0 |
| sonnet | v2-07-cmake-cross | A | 2 | e08461f | pass | True | True | True | 48.0 | 16 | 15 (0/13) | 412887 | 5307 | 0.1633 |  | 0 |
| sonnet | v2-07-cmake-cross | C | 2 | ecb26c9 | pass | True | True | True | 52.0 | 21 | 20 (4/13) | 634181 | 5224 | 0.2013 |  | 0 |
| sonnet | v2-07-cmake-cross | A | 3 | ee5a53e | pass | True | True | True | 43.8 | 15 | 14 (0/13) | 417926 | 4784 | 0.1552 |  | 0 |
| sonnet | v2-07-cmake-cross | C | 3 | e255f93 | pass | True | True | True | 66.3 | 24 | 23 (5/16) | 835887 | 6793 | 0.2493 |  | 0 |
| sonnet | v2-07-cmake-cross | A | 4 | e36b830 | pass | True | True | True | 43.5 | 16 | 15 (0/13) | 411971 | 4457 | 0.15 |  | 0 |
| sonnet | v2-07-cmake-cross | C | 4 | e4112b7 | pass | True | True | True | 66.5 | 27 | 26 (7/14) | 765933 | 6781 | 0.2465 |  | 0 |
| sonnet | v2-07-cmake-cross | A | 5 | e4b8b3d | pass | True | True | True | 55.0 | 16 | 15 (0/14) | 444656 | 5099 | 0.1611 |  | 0 |
| sonnet | v2-07-cmake-cross | C | 5 | e4748e3 | pass | True | True | True | 73.4 | 23 | 22 (5/14) | 723974 | 6328 | 0.2336 |  | 0 |
| sonnet | v2-08-fd-leak | A | 1 | e0b9c67 | pass | True | True | True | 32.8 | 9 | 8 (0/6) | 204263 | 2976 | 0.1062 |  | 0 |
| sonnet | v2-08-fd-leak | C | 1 | e19355b | pass | True | True | True | 66.4 | 30 | 29 (19/6) | 714343 | 6770 | 0.25 |  | 0 |
| sonnet | v2-08-fd-leak | A | 2 | e8eb81e | pass | True | True | True | 144.0 | 8 | 7 (0/5) | 158650 | 2748 | 0.0936 |  | 0 |
| sonnet | v2-08-fd-leak | C | 2 | e2e16ba | pass | True | True | True | 60.2 | 28 | 27 (17/7) | 599466 | 6622 | 0.2268 |  | 0 |
| sonnet | v2-08-fd-leak | A | 3 | e0a1178 | pass | True | True | True | 41.8 | 9 | 8 (0/7) | 218908 | 3281 | 0.1042 |  | 0 |
| sonnet | v2-08-fd-leak | C | 3 | e915118 | pass | True | True | True | 71.8 | 28 | 27 (14/7) | 660653 | 6069 | 0.2183 |  | 0 |
| sonnet | v2-08-fd-leak | A | 4 | e125ca6 | pass | True | True | True | 41.8 | 8 | 7 (0/5) | 169562 | 3239 | 0.1053 |  | 0 |
| sonnet | v2-08-fd-leak | C | 4 | e8ec842 | pass | True | True | True | 68.7 | 28 | 27 (17/7) | 693325 | 6843 | 0.2328 |  | 0 |
| sonnet | v2-08-fd-leak | A | 5 | e2838d1 | pass | True | True | True | 48.1 | 11 | 10 (0/8) | 265753 | 4187 | 0.1256 |  | 0 |
| sonnet | v2-08-fd-leak | C | 5 | e71f6a9 | pass | True | True | True | 62.2 | 26 | 25 (15/7) | 694528 | 6143 | 0.2211 |  | 0 |

# Per task and arm

| model | task | arm | n | oracle pass | root cause | median wall s | median turns | median tool calls | median cost USD |
|---|---|---|---|---|---|---|---|---|---|
| haiku | v2-01-config-overflow | A | 5 | 5/5 | 5/5 | 97.4 | 22 | 21 | 0.02 |
| haiku | v2-01-config-overflow | C | 5 | 5/5 | 5/5 | 98.3 | 33 | 32 | 0.03 |
| haiku | v2-02-mp-deadlock | A | 5 | 5/5 | 5/5 | 192.4 | 14 | 13 | 0.01 |
| haiku | v2-02-mp-deadlock | C | 5 | 5/5 | 4/5 | 176.6 | 45 | 44 | 0.03 |
| haiku | v2-03-resmgr-slog2 | A | 5 | 5/5 | 5/5 | 111.1 | 17 | 16 | 0.02 |
| haiku | v2-03-resmgr-slog2 | C | 5 | 5/5 | 5/5 | 88.0 | 32 | 31 | 0.02 |
| haiku | v2-04-deep-expr | A | 5 | 5/5 | 5/5 | 103.1 | 18 | 17 | 0.02 |
| haiku | v2-04-deep-expr | C | 5 | 5/5 | 5/5 | 118.2 | 40 | 39 | 0.03 |
| haiku | v2-05-hang-states | A | 5 | 5/5 | 5/5 | 212.8 | 14 | 13 | 0.02 |
| haiku | v2-05-hang-states | C | 5 | 5/5 | 5/5 | 121.7 | 35 | 34 | 0.03 |
| haiku | v2-06-worker-null | A | 5 | 5/5 | 5/5 | 69.1 | 16 | 15 | 0.02 |
| haiku | v2-06-worker-null | C | 5 | 5/5 | 5/5 | 70.2 | 32 | 31 | 0.02 |
| haiku | v2-07-cmake-cross | A | 5 | 5/5 | 5/5 | 153.5 | 42 | 41 | 0.04 |
| haiku | v2-07-cmake-cross | C | 5 | 5/5 | 5/5 | 137.6 | 43 | 42 | 0.04 |
| haiku | v2-08-fd-leak | A | 5 | 5/5 | 5/5 | 77.0 | 12 | 11 | 0.01 |
| haiku | v2-08-fd-leak | C | 5 | 5/5 | 5/5 | 103.0 | 33 | 32 | 0.03 |
| sonnet | v2-01-config-overflow | A | 5 | 5/5 | 5/5 | 35.2 | 11 | 10 | 0.13 |
| sonnet | v2-01-config-overflow | C | 5 | 5/5 | 5/5 | 44.2 | 18 | 17 | 0.19 |
| sonnet | v2-02-mp-deadlock | A | 5 | 5/5 | 5/5 | 38.3 | 11 | 10 | 0.11 |
| sonnet | v2-02-mp-deadlock | C | 5 | 5/5 | 5/5 | 132.0 | 33 | 32 | 0.27 |
| sonnet | v2-03-resmgr-slog2 | A | 5 | 5/5 | 5/5 | 171.9 | 10 | 9 | 0.15 |
| sonnet | v2-03-resmgr-slog2 | C | 5 | 5/5 | 5/5 | 46.8 | 24 | 23 | 0.2 |
| sonnet | v2-04-deep-expr | A | 5 | 5/5 | 4/5 | 32.1 | 10 | 9 | 0.12 |
| sonnet | v2-04-deep-expr | C | 5 | 5/5 | 5/5 | 34.4 | 14 | 13 | 0.16 |
| sonnet | v2-05-hang-states | A | 5 | 5/5 | 5/5 | 44.4 | 8 | 7 | 0.09 |
| sonnet | v2-05-hang-states | C | 5 | 5/5 | 5/5 | 49.7 | 18 | 17 | 0.16 |
| sonnet | v2-06-worker-null | A | 5 | 5/5 | 5/5 | 32.7 | 10 | 9 | 0.13 |
| sonnet | v2-06-worker-null | C | 5 | 5/5 | 5/5 | 38.7 | 18 | 17 | 0.18 |
| sonnet | v2-07-cmake-cross | A | 5 | 5/5 | 5/5 | 48.0 | 16 | 15 | 0.16 |
| sonnet | v2-07-cmake-cross | C | 5 | 5/5 | 5/5 | 66.3 | 23 | 22 | 0.23 |
| sonnet | v2-08-fd-leak | A | 5 | 5/5 | 5/5 | 41.8 | 9 | 8 | 0.11 |
| sonnet | v2-08-fd-leak | C | 5 | 5/5 | 5/5 | 66.4 | 28 | 27 | 0.23 |
| haiku | all | A | 40 | 40/40 | 40/40 | 103.05 | 16.5 | 15.5 | 0.02 |
| haiku | all | C | 40 | 40/40 | 39/40 | 114.95 | 37.0 | 36.0 | 0.03 |
| sonnet | all | A | 40 | 40/40 | 39/40 | 41.75 | 10.0 | 9.0 | 0.12 |
| sonnet | all | C | 40 | 40/40 | 40/40 | 50.6 | 20.5 | 19.5 | 0.2 |

# Cost and duration

```
{
  "trials": 160,
  "scored": 160,
  "cost_total_usd": 15.31,
  "cost_mean_usd": 0.096,
  "cost_max_usd": 0.3538,
  "wall_mean_s": 97.3,
  "wall_max_s": 746.3,
  "judge_cost_usd": 0.9037
}
```
