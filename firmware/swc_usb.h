#ifndef _SWC_USB_H_
#define _SWC_USB_H_

/* Includes: */
#include <stdbool.h>

#include <avr/wdt.h>
#include <avr/power.h>
#include <avr/interrupt.h>

#include "Descriptors.h"
#include <LUFA/Drivers/USB/USB.h>

/* Function Prototypes: */
void send_feedback(char const *fmt, ...);
void setup_hardware(void);
void activate_ports(void);
void deactivate_ports(void);
void USB_tasks(void);
uint8_t read_status(void);
uint8_t read_control(void);
void write_data(uint8_t byte);
void write_control(uint8_t byte);
void flip_led(void);
void cdc_device_receive_bytes(uint8_t *buffer, uint8_t length);
void write_rom(uint16_t total_blocks, uint8_t emu_mode_select);
void wait_busy_bit(bool bit, uint8_t poll_min);
void send_byte(uint8_t byte);
void send_command(uint8_t command_code, uint16_t address, uint16_t length);
void send_block(uint16_t address, uint16_t block_size);
void send_command0(uint16_t address, uint8_t byte);
void read_sram(void);
void write_sram(uint16_t total_bytes);
bool receive_block(uint16_t address, uint16_t len);
uint8_t receive_byte(void);
void EVENT_USB_Device_ConfigurationChanged(void);
void EVENT_USB_Device_ControlRequest(void);

#endif
