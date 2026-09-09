# This file is part of Telegram Desktop,
# the official desktop application for the Telegram messaging service.
#
# For license and copyright information please follow this link:
# https://github.com/telegramdesktop/tdesktop/blob/master/LEGAL

add_executable(test_ayu_premium_promo_policy)
init_target(test_ayu_premium_promo_policy "(tests)")

target_include_directories(test_ayu_premium_promo_policy PRIVATE ${src_loc})

nice_target_sources(test_ayu_premium_promo_policy ${src_loc}
PRIVATE
    ayu/premium_promo_policy.h
    tests/test_ayu_premium_promo_policy.cpp
)

set_target_properties(test_ayu_premium_promo_policy PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}
)

add_dependencies(Telegram test_ayu_premium_promo_policy)
