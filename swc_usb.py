#!/usr/bin/env python
from collections import namedtuple
from io import BytesIO
from time import sleep, time

import serial
import click
from serial.tools import list_ports
from struct import pack, unpack_from

BLOCK_SIZE = 8192
SWC_HEADER_SIZE = 512
LO_ROM = 'lo_rom'
HI_ROM = 'hi_rom'


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
            raise click.ClickException('Transfer timeout, is the USB adapter connected to the Super Wild Card? Power cycle the SNES might fix this.')
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
@click.option('--hirom/--lorom', default=None, help="Run the ROM in HiROM or LoROM mode.")
@click.option('--sram-size',  type=click.Choice(['0', '16', '64']), default=None, help="Set SRAM size for ROM in Kibit e.g. --sram-size=64")
@click.argument('rom_file', type=click.File('rb'))
@click.pass_obj
def send_rom(ctx, hirom, sram_size, rom_file):
    """Send a ROM file to the Super Wild Card."""
    rom = rom_file.read()
    _, rom = separate_swc_header(rom)
    rom_type, snes_header_sram_size = determine_rom_type_and_sram_size(rom)
    if hirom is not None:
        rom_type = HI_ROM if hirom else LO_ROM
    if sram_size:
        emu_byte_sram_size = (int(sram_size) * 1024) // 8
    else:
        emu_byte_sram_size = snes_header_sram_size
    emu_byte = emulation_mode_select(rom_type=rom_type, sram_size=emu_byte_sram_size)
    blocks = len(rom) // BLOCK_SIZE
    command = pack('>10sxHB', b'WRITE ROM', blocks, emu_byte)
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
        sram = BytesIO()
        with click.progressbar(range(0, BLOCK_SIZE * 4), label='Receiving') as all_bytes:
            for _ in all_bytes:
                byte = ser.read(size=1)
                if byte == b'':
                    raise click.ClickException('Transfer timeout.')
                sram.write(byte)
        trail = ser.read(size=9)
        if trail != b'*#*#*#*OK':
            raise click.ClickException('Transfer failed! ({})'.format(trail))
        sram_file.write(sram.getbuffer())
        click.echo(click.style('Transfer complete in {0:.2f} seconds.'.format(time() - start_time), fg='green'))


def emulation_mode_select(rom_type, sram_size):
    emu_byte = 0x00
    if rom_type == HI_ROM:
        emu_byte |= 0x30

    bits_by_sram_size = {
        8192: 0x04,
        2048: 0x08,
        0: 0x0C,
    }

    emu_byte |= bits_by_sram_size.get(sram_size, 0x00)
    return emu_byte


def separate_swc_header(rom):
    swc_header = namedtuple('SwcHeader', ('emulation', 'id1', 'id2', 'type'))(*unpack_from('2xB5x3B', rom))
    if (
        swc_header.id1 == 0xAA and
        swc_header.id2 == 0xBB and
        swc_header.type in (0x04, 0x05) and
        len(rom) % BLOCK_SIZE == SWC_HEADER_SIZE
    ):
        return swc_header, rom[SWC_HEADER_SIZE:]
    else:
        return None, rom


def determine_rom_type_and_sram_size(rom):
    lo_header = parse_header(rom[0x7FC0:0x7FFF])
    hi_header = parse_header(rom[0xFFC0:0xFFFF])
    rom_size = len(rom)
    lo_rom_rank = rank_header(header=lo_header, expected_rom_type=LO_ROM, rom_size=rom_size)
    hi_rom_rank = rank_header(header=hi_header, expected_rom_type=HI_ROM, rom_size=rom_size)

    if hi_rom_rank > lo_rom_rank:
        return HI_ROM, snes_header_size_in_bytes(hi_header.sram_size)
    else:
        return LO_ROM, snes_header_size_in_bytes(lo_header.sram_size)


def rank_header(header, expected_rom_type, rom_size):
    rank = 0
    markup_bytes = {
        LO_ROM: [0x20, 0x30, 0x32],
        HI_ROM: [0x21, 0x31, 0x35],
    }
    sram_sizes = [2048, 4096, 8192]

    if header.rom_makeup_byte in markup_bytes[expected_rom_type]:
        rank += 1

    if all(0x1F < byte < 0x7F for byte in header.game_title):
        rank += 1

    if snes_header_size_in_bytes(header.rom_size) == rom_size:
        rank += 1

    if snes_header_size_in_bytes(header.sram_size) in sram_sizes:
        rank += 1

    if header.country < 14:
        rank += 1

    if header.checksum ^ header.checksum_complement == 0xFFFF:
        rank += 1
    return rank


def snes_header_size_in_bytes(size_byte):
    if size_byte == 0:
        return 0
    else:
        return 0x400 << size_byte


def parse_header(header_bytes):
    header = namedtuple(
        'SnesHeader',
        (
            'game_title',
            'rom_makeup_byte',
            'rom_type',
            'rom_size',
            'sram_size',
            'country',
            'license',
            'version',
            'checksum_complement',
            'checksum',
        )
    )(*unpack_from('21s7B2H', header_bytes[:32]))
    return header


if __name__ == '__main__':
    main(obj={})
