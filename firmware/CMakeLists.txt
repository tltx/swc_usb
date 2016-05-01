cmake_minimum_required(VERSION 3.3)
project(swc_usb)

set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -std=c++11")

add_custom_target(swc_usb COMMAND make -C ${swc_usb_SOURCE_DIR} CLION_EXE_DIR=${PROJECT_BINARY_DIR})

include_directories("/usr/local/opt/avr-gcc/avr/include" "." "./Config")

add_definitions(-DUSE_LUFA_CONFIG_HEADER)
add_definitions(-DUSB_CAN_BE_DEVICE)
add_definitions(-D__AVR_ATmega32U4__)

set(SOURCE_FILES
        Descriptors.h
        Descriptors.c
        swc_usb.h
        swc_usb.c
        ./Config/LUFAConfig.h
        )

add_executable(swc_usb.hex ${SOURCE_FILES})
