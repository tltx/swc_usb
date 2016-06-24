#include "swc_usb.h"

USB_ClassInfo_CDC_Device_t VirtualSerial_CDC_Interface =
    {
        .Config =
            {
                .ControlInterfaceNumber   = INTERFACE_ID_CDC_CCI,
                .DataINEndpoint           =
                    {
                        .Address          = CDC_TX_EPADDR,
                        .Size             = CDC_TXRX_EPSIZE,
                        .Banks            = 1,
                    },
                .DataOUTEndpoint =
                    {
                        .Address          = CDC_RX_EPADDR,
                        .Size             = CDC_TXRX_EPSIZE,
                        .Banks            = 1,
                    },
                .NotificationEndpoint =
                    {
                        .Address          = CDC_NOTIFICATION_EPADDR,
                        .Size             = CDC_NOTIFICATION_EPSIZE,
                        .Banks            = 1,
                    },
            },
    };

static FILE USBSerialStream;

bool timeout = false;
#define COMMAND_LEN 11

int main(void) {
    setup_hardware();

    /* Create a regular character stream for the interface so that it can be used with the stdio.h functions */
    CDC_Device_CreateStream(&VirtualSerial_CDC_Interface, &USBSerialStream);

    GlobalInterruptEnable();

    uint8_t raw_command[COMMAND_LEN];
    uint8_t arguments[3];

    for (; ;) {
        cdc_device_receive_bytes(raw_command, COMMAND_LEN);
        raw_command[COMMAND_LEN-1] = '\0';
        char *command = (char *) raw_command;

        activate_ports();
        if (strcmp(command, "WRITE ROM") == 0) {
            cdc_device_receive_bytes(arguments, 3);
            uint16_t total_blocks = (arguments[0] << 8) | arguments[1];
            uint8_t emu_mode_select = arguments[2];
            write_rom(total_blocks, emu_mode_select);
        }
        else if (strcmp(command, "READ SRAM") == 0) {
            read_sram();
        }
        else if (strcmp(command, "WRITE SRAM") == 0) {
            cdc_device_receive_bytes(arguments, 2);
            uint16_t total_bytes = (arguments[0] << 8) | arguments[1];
            write_sram(total_bytes);
        }
        else {
            send_feedback("UNKNOWN COMMAND\n");
        }
        deactivate_ports();
        if (timeout) {
            send_feedback("TIMEOUT\n");
            timeout = false;
        }
        while (CDC_Device_ReceiveByte(&VirtualSerial_CDC_Interface) > -1) {
            USB_tasks();
        }
        USB_tasks();
    }
}

void cdc_device_receive_bytes(uint8_t *buffer, uint8_t length) {
    for (uint8_t bytes_received = 0; length > bytes_received;) {
        int16_t received_byte = CDC_Device_ReceiveByte(&VirtualSerial_CDC_Interface);
        if (received_byte >= 0) {
            buffer[bytes_received++] = (uint8_t) received_byte;
        }
        USB_tasks();
    }
}

void setup_hardware(void) {
    /* Disable watchdog if enabled by bootloader/fuses */
    MCUSR &= ~(1 << WDRF);
    wdt_disable();

    /* Disable JTAG to free up PORTF pins */
    JTAG_DISABLE();

    /* Disable clock division */
    clock_prescale_set(clock_div_1);

    /* Hardware Initialization */
    USB_Init();
}

void activate_ports(void) {
    /* Set port D as output for Data */
    PORTD = 0x00;
    DDRD = 0xFF;

    /* Set port B as input for Status */
    PORTB = 0xFF; //enable all pull-ups
    DDRB = 0x00;

    /* Set port F as output for Control */
    PORTF = 0x00;
    DDRF = 0xF0;

    /* Set LED */
    PORTE = 0x40;
    DDRE = 0x40;
}

void deactivate_ports(void) {
    /* Set port D to input */
    PORTD = 0x00;
    DDRD = 0x00;

    /* Set port B to input */
    PORTB = 0x00; //disable all pull-ups
    DDRB = 0x00;

    /* Set port F to input */
    PORTF = 0x00;
    DDRF = 0x00;

    /* Set LED */
    PORTE = 0x00;
    DDRE = 0x00;
}

void send_feedback(char const *fmt, ...) {
    va_list argptr;
    va_start(argptr, fmt);
    vfprintf(&USBSerialStream, fmt, argptr);
    va_end(argptr);
    CDC_Device_Flush(&VirtualSerial_CDC_Interface);
}

void USB_tasks(void) {
    CDC_Device_USBTask(&VirtualSerial_CDC_Interface);
    USB_USBTask();
}

/** Event handler for the library USB Configuration Changed event. */
void EVENT_USB_Device_ConfigurationChanged(void) {
    CDC_Device_ConfigureEndpoints(&VirtualSerial_CDC_Interface);
}

/** Event handler for the library USB Control Request reception event. */
void EVENT_USB_Device_ControlRequest(void) {
    CDC_Device_ProcessControlRequest(&VirtualSerial_CDC_Interface);
}

/* SWC stuff */
#define PARPORT_INPUT_MASK 0x78
#define PARPORT_IBUSY 0x80
#define PARPORT_STROBE 0x1
#define POLL_MAX 65534
#define BLOCK_SIZE 8192                // don't change, only 8192 works!

void write_data(uint8_t byte) {
    /* Port D
     *  dsub25		register
     *  2 to 9		D0+ to D7+
     */
    PORTD = byte;
}

void write_control(uint8_t byte) {
    /* Port F 4-7
     *  dsub25  name    register (- means inverted signal high=0)
     * 	17      Select  C3-
     * 	16      Init    C2+
     * 	14      AutoFd  C1-
     * 	 1      Strobe  C0-
     */
    PORTF = (byte ^ 0b00001011) << 4;
}

uint8_t read_control(void) {
    //Port F 4-7
    return (PINF >> 4) ^ 0b00001011;
}

uint8_t read_status(void) {
    /* Port B
     *  dsub25  name       register (- means inverted signal high=0)
     *	11      Busy       S7-
     *	10      Ack        S6+
     *	12      PaperEnd   S5+
     *	13      SelectIn   S4+
     *	15      Error      S3+
     */
    return PINB ^ PARPORT_IBUSY;
}

void wait_busy_bit(bool bit, uint8_t poll_min) {
    bool busy_bit = false;
    uint16_t poll_count = 0;

    if (timeout) {
        return;
    }

    do {
        busy_bit = (bool) (read_status() & PARPORT_IBUSY);
        ++poll_count;
    } while (poll_count < poll_min || (busy_bit != bit && poll_count < POLL_MAX));

    if (busy_bit != bit) {
        timeout = true;
    }
}

void flip_led(void) {
    PORTE = PORTE ^ 0x40;
}

void invert_strobe(void) {
    write_control(read_control() ^ PARPORT_STROBE);
    flip_led();
}

void send_byte(uint8_t byte) {
    wait_busy_bit(1, 0);
    write_data(byte);
    invert_strobe();
    wait_busy_bit(1, 0); // necessary if followed by receive_byte()
}

void send_command(uint8_t command_code, uint16_t address, uint16_t length) {
    send_byte(0xD5);
    send_byte(0xAA);
    send_byte(0x96);
    send_byte(command_code);
    send_byte(address);
    send_byte(address >> 8);
    send_byte(length);
    send_byte(length >> 8);
    send_byte(0x81 ^ command_code ^ address ^ (address >> 8) ^ length ^ (length >> 8)); // checksum
}

void send_command0(uint16_t address, uint8_t byte) {
    /* command 0 for 1 byte */
    send_command(0, address, 1);
    send_byte(byte);
    send_byte(0x81 ^ byte);
}

void send_block(uint16_t address, uint16_t block_size) {
    uint8_t checksum = 0x81;

    send_command(0, address, block_size);
    for (uint16_t n = 0; n < block_size;) {
        USB_tasks();
        uint16_t number_of_bytes_ready = CDC_Device_BytesReceived(&VirtualSerial_CDC_Interface);
        for (uint16_t i = 0; i < number_of_bytes_ready; ++i) {
            uint8_t byte = (uint8_t) CDC_Device_ReceiveByte(&VirtualSerial_CDC_Interface);
            send_byte(byte);
            checksum ^= byte;
            ++n;
        }
    }
    send_byte(checksum);
}

void write_rom(uint16_t total_blocks, uint8_t emu_mode_select) {
    uint16_t block = 0;
    uint16_t address = 0x200;

    for (; block < total_blocks; ++block) {
        send_command0(0xC010, block >> 9);
        send_command(5, address, 0);
        send_block(0x8000, BLOCK_SIZE);
        ++address;
        if (timeout) {
            return;
        }
    }

    if (block > 0x200)
        send_command0(0xC010, 1);

    send_command(5, 0, 0);
    send_command(6, 5 | (total_blocks << 8), total_blocks >> 8); // bytes: 6, 5, #8 K L, #8 K H, 0
    send_command(6, 1 | (emu_mode_select << 8), 0); // last arg = 1 enables RTS mode, 0 disables it

    send_feedback("OK\n");
}

uint8_t receive_byte(void) {
    wait_busy_bit(0, 3);
    uint8_t byte = (read_status() & PARPORT_INPUT_MASK) >> 3; // receive low nibble
    invert_strobe();
    wait_busy_bit(0, 3);
    byte |= (read_status() & PARPORT_INPUT_MASK) << 1; // receive high nibble
    invert_strobe();
    return byte;
}

bool receive_block(uint16_t address, uint16_t len) {
    uint8_t checksum = 0x81;
    send_command(1, address, len);
    for (uint16_t n = 0; n < len; ++n) {
        uint8_t byte = receive_byte();
        CDC_Device_SendData(&VirtualSerial_CDC_Interface, &byte, 1);
        checksum ^= byte;
    }
    return checksum != receive_byte();  //Compare calculated checksum with received checksum
}

void read_sram(void) {
    send_command(5, 0, 0);
    send_command0(0xE00D, 0);
    send_command0(0xC008, 0);

    uint8_t blocks_left = 4;  // SRAM is 4*8 KiB
    uint16_t address = 0x100;
    uint8_t error_count = 0;
    while (blocks_left > 0) {
        send_command(5, address, 0);
        error_count += receive_block(0x2000, BLOCK_SIZE);
        _delay_ms(50);
        --blocks_left;
        ++address;
        if (timeout) {
            return;
        }
    }
    CDC_Device_Flush(&VirtualSerial_CDC_Interface);
    if (error_count) {
        send_feedback("*#*#*ERR%s\n", error_count);
    }
    else {
        send_feedback("*#*#*#*OK\n");
    }
}

void write_sram(uint16_t total_bytes) {
    send_command(5, 0, 0);
    send_command0(0xE00D, 0);
    send_command0(0xC008, 0);

    uint16_t address = 0x100;
    uint16_t last_block_size = total_bytes % BLOCK_SIZE;
    uint8_t blocks = total_bytes / BLOCK_SIZE;
    if (last_block_size) {
        ++blocks;
    }
    uint16_t block_size = BLOCK_SIZE;
    for (uint8_t block = 0; block < blocks; ++block) {
        if (last_block_size && block == (blocks - 1)) {
            block_size = last_block_size;
        }
        send_command(5, address, 0);
        send_block(0x2000, block_size);
        ++address;
        if (timeout) {
            return;
        }
    }
    send_feedback("OK\n");
}
