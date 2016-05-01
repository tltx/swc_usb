#!/usr/bin/env python
from collections import namedtuple
from time import sleep, time

import serial
import click
from serial.tools import list_ports
from struct import pack, unpack_from

BLOCK_SIZE = 8192
SWC_HEADER_SIZE = 512


def detect_com_port():
    for port in list_ports.comports():
        if port.vid == 0x03EB and port.pid == 0x2044:
            return port.device
    raise click.ClickException('Could not auto detect the SWC USB adapter.')


def send(com_port, command, data):
    start_time = time()
    with serial.Serial(com_port, timeout=0) as ser:
        ser.write(command)
        feedback = b''
        with click.progressbar(range(0, len(data), BLOCK_SIZE), label='Sending') as block_offsets:
            for block_start in block_offsets:
                ser.write(data[block_start:block_start + BLOCK_SIZE])
                feedback = check_feedback(ser, feedback)
            ser.flush()
            check_feedback(ser, feedback, wait_ok=30)
    click.echo(click.style('Transfer complete in {0:.2f} seconds.'.format(time() - start_time), fg='green'))


def check_feedback(ser, feedback, wait_ok=0):
    while True:
        feedback += ser.read(size=100)
        if b'OK' in feedback:
            break
        elif b'TIMEOUT' in feedback:
            raise click.ClickException('Transfer timeout, is the USB adapter connected to the Super Wild Card?')
        elif b'UNKNOWN COMMAND' in feedback:
            raise click.ClickException('Transfer failed!')
        if wait_ok > 0:
            wait_ok -= 1
            if wait_ok == 0:
                raise click.ClickException('Transfer failed!')
            sleep(0.1)
        else:
            break
    return feedback


@click.group()
@click.option('--com-port', default=detect_com_port, help='The USB CDC virtual serial port of the adapter. Only needed if auto detect does not work.')
@click.pass_context
def main(ctx, com_port):
    """Program that communicates with the Super Wild Card via a custom USB adapter."""
    ctx.obj['com_port'] = com_port


@main.command(name='send-rom')
@click.option('--hirom/--lorom', default=None, help="Run the ROM in hiROM or loROM mode.")
@click.option('--sram-size', default=None, help="Set SRAM size for ROM in Kibibytes e.g. --sram-size=8")
@click.argument('rom_file', type=click.File('rb'))
@click.pass_obj
def send_rom(ctx, hirom, sram_size, rom_file):
    """Send a ROM file to the Super Wild Card."""
    rom = rom_file.read()
    header, rom = separate_swc_header(rom)
    emu_bit = adjust_header_emu_bit(header.emulation) if header else 0x00
    emu_bit = emulation_mode_select(emu_bit, hirom, sram_size)
    blocks = len(rom) // BLOCK_SIZE
    command = pack('>10sxHB', b'WRITE ROM', blocks, emu_bit)
    send(ctx['com_port'], command, rom)


@main.command(name='send-sram')
@click.argument('sram_file', type=click.File('rb'))
@click.pass_obj
def send_sram(ctx, sram_file):
    """Send a SRAM file to the Super Wild Card."""
    sram = sram_file.read()
    _, sram = separate_swc_header(sram)
    total_bytes = len(sram)
    command = pack('>10sxH', b'WRITE SRAM', total_bytes)
    send(ctx['com_port'], command, sram)


@main.command(name='fetch-sram')
@click.argument('sram_file', type=click.File('wb'))
@click.pass_obj
def fetch_sram(ctx, sram_file):
    """Fetch a SRAM dump from the Super Wild Card."""
    with serial.Serial(ctx['com_port'], timeout=3) as ser:
        start_time = time()
        ser.write(pack('>10sx', b'READ SRAM'))
        sram = bytearray()
        with click.progressbar(range(0, BLOCK_SIZE * 4), label='Receiving') as all_bytes:
            for _ in all_bytes:
                byte = ser.read(size=1)
                if byte == b'':
                    raise click.ClickException('Transfer timeout.')
                sram.append(byte)
        trail = ser.read(size=9)
        if trail != '*#*#*#*OK':
            raise click.ClickException('Transfer failed! ({})'.format(trail))
        sram_file.write(sram)
        click.echo(click.style('Transfer complete in {0:.2f} seconds.'.format(time() - start_time), fg='green'))


def emulation_mode_select(emu_bit, hirom, sram_size):
    if hirom is not None:
        emu_bit = emu_bit | 0x30 if hirom else emu_bit & ~0x30

    bits_by_sram_size = {
        # bit 3 & 2 are already ok for 32 KiB SRAM size
        8: 0x04,
        2: 0x08,
        0: 0x0C,
    }
    if sram_size is not None:
        sram_bit_mask = 0xF3
        emu_bit = (emu_bit & sram_bit_mask) | bits_by_sram_size.get(sram_size, 0x00)
    return emu_bit


def separate_swc_header(rom):
    swc_header = namedtuple('SwcHeader', ('emulation', 'id1', 'id2', 'type'))(*unpack_from('2xB5xBBB', rom))
    if (
        swc_header.id1 == 0xAA and
        swc_header.id2 == 0xBB and
        swc_header.type in (0x04, 0x05) and
        len(rom) % BLOCK_SIZE == SWC_HEADER_SIZE
    ):
        return swc_header, rom[SWC_HEADER_SIZE:]
    else:
        return None, rom


def adjust_header_emu_bit(emu_bit):
    # 0x0c == no SRAM & LoROM; we use the header, so that the user can override this
    # bit 4 == 0 => DRAM mode 20 (LoROM); disable SRAM by setting SRAM mem map mode 2
    if emu_bit & 0x1C == 0x0C:
        emu_bit |= 0x20
    return emu_bit


if __name__ == '__main__':
    main(obj={})
