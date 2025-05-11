import serial.tools.list_ports
import sys
import serial
import time
import argparse
import threading
import os
import signal
import ctypes
import queue
import struct


PACKET_HEADER = 0xa5
# cmd define
CMD_NEW_FILE = 10 # start send new file
CMD_FILE_LEN = 11 # the length of the comming file
CMD_DATA_PKT = 20 # this is a data packet of the sending
CMD_SEND_CPL = 30 # files send completed
CMD_NEXT_PKT = 21 # ready for receiving new packet


stop_event = threading.Event()
recvQueue = queue.Queue()
sendQueue = queue.Queue()
fileLen = 0


def SetTip(tip):
    ctypes.windll.kernel32.SetConsoleTitleW(tip)

def ShowProgress(nowCnt, totalCnt):
    if not hasattr(ShowProgress, "_last_time"):
        ShowProgress._last_time = time.time()
        ShowProgress._last_cnt = 0
        ShowProgress._start_time = time.time()

    current_time = time.time()
    time_elapsed = current_time - ShowProgress._last_time
    if time_elapsed >= 2.0:
        byte_diff = nowCnt - ShowProgress._last_cnt
        bandwidth = byte_diff / time_elapsed
        bandwidth = int(bandwidth)
        percent = 100 * nowCnt / totalCnt
        percent = int(percent*100)/100
        ShowProgress._last_time = current_time
        ShowProgress._last_cnt = nowCnt
        SetTip("tx: " + str(nowCnt) + "/" + str(totalCnt) + " " + str(percent) + "% " + str(bandwidth) + "B/s")


def print_bytes_hex(data):
    # print("\r\n")
    lin = ['%02X' % i for i in data]
    print(" ".join(lin), end=' ')
    sys.stdout.flush()
    

def interrupt_handler(signum, frame):
    print("got event: ", str(signum))
    stop_event.set()


def DumpQueue():
    temp_data = []
    while not recvQueue.empty():
        item = recvQueue.get()
        temp_data.append(item)
    print_bytes_hex(temp_data)


def ReadSerialHandler(ser):
    while not stop_event.is_set():
        if ser.in_waiting > 0:
            data = ser.read(ser.in_waiting)
            # print_bytes_hex(data)
            for byte in data:
                recvQueue.put(byte)
            # DumpQueue()
        time.sleep(0.01)


def WriteSerialHandler(ser):
    while not stop_event.is_set():
        if not sendQueue.empty():
            data_to_send = sendQueue.get()
            ser.write(data_to_send)
            ser.flush()
        # time.sleep(0.1)
        

def readOneByte():
    if recvQueue.empty():
        return None
    else:
        b = recvQueue.get(1)
        return b


def WriteSerialData(data):
    sendQueue.put(data)


def readPacket():
    # print("\r\nwait new packet")
    sanityCntMax = 200
    sanityCnt = sanityCntMax
    packetLen = 0
    packetFsm = 0
    data = bytearray()
    chksum = 0
    while sanityCnt > 0:
        sanityCnt -= 1
        if sanityCnt == 0:
            # print("\r\npacket overtime")
            break

        byteData = readOneByte()
        if byteData == None:
            time.sleep(0.01)
            print(".", flush=True, end='')
            continue
        sanityCnt = sanityCntMax

        # print(" " + str(byteData), end='', flush=True)
        if packetFsm == 0:
            if byteData == PACKET_HEADER:
                chksum += byteData
                chksum &= 0xff
                packetFsm = 1
            continue
        
        elif packetFsm == 1:
            packetLen = byteData << 8
            chksum += byteData
            chksum &= 0xff
            packetFsm = 2

        elif packetFsm == 2:
            packetLen += byteData
            chksum += byteData
            chksum &= 0xff
            # print("\r\nmsg len2: ", str(packetLen), flush=True)
            packetFsm = 3

        elif packetFsm == 3:
            data.append(byteData)
            packetLen -= 1
            chksum += byteData
            chksum &= 0xff
            # print("=" + str(packetLen), flush=True)
            if packetLen == 0:
                packetFsm = 4

        elif packetFsm == 4:
            packetFsm = 0
            if byteData == chksum:
                return True, data
            else:
                print("chck1: ", str(chksum))
                print("chck2: ", str(byteData))

        else:
            print("odd fsm")
            packetFsm = 0
    
    return False, data



def calculate_checksum(data):
    return sum(data) & 0xFF  # 求和后取低8位


def CreatePacketData(cmd, body=None):
    if body is None:
        body = bytearray()
    packet = bytearray()
    packet.append(cmd)
    packet.extend(body)
    return packet

def CreatePacket(data):
    packetData = bytearray()
    packetData.append(PACKET_HEADER)
    # packetData.append(len(data))
    length = len(data)
    lendata = struct.pack('>H', length) # use 2 bytes to present the length
    packetData.extend(lendata)
    packetData.extend(data)
    chksum = calculate_checksum(packetData)
    packetData.append(chksum)
    return packetData

def SendCmd(cmdNum, msgData=None):
    # print(">>>>", end='')
    d = CreatePacketData(cmdNum, msgData)
    # print_bytes_hex(d)
    dp = CreatePacket(d)
    print("\r\nsend cmd: " + str(cmdNum), end='', flush=True)
    # print_bytes_hex(dp)
    WriteSerialData(dp)


def RecvCmdPacket():
    waitMax = 3
    while (not stop_event.is_set()) and (waitMax >= 0):
        waitMax -= 1
        sta, data = readPacket()
        # print("read packet back...")
        if sta == False:
            continue
        # if cmd == data[0]:
        return True, data
        # else:
        #     return False, data
        
    return False, None


def WaitCmd(cmd):
    print("\r\nwait cmd: " + str(cmd), end='', flush=True)
    s, d = RecvCmdPacket()
    if s == True:
        if cmd == d[0]:
            return True
    return False


def RecvFile1(recvFilePath):
    print("waiting packets...")
    recvedFile = open(recvFilePath, 'wb')
    recvCnt = 0
    fsmRecvFile = 0
    while not stop_event.is_set():
        if fsmRecvFile == 0:
            s, d = RecvCmdPacket(CMD_NEW_FILE)
            if s == True:
                recvedFile = open(recvFilePath, 'wb')
                SendCmd(CMD_NEXT_PKT)
                fsmRecvFile = 1

        elif fsmRecvFile == 1:
            s, d = RecvCmdPacket(CMD_FILE_LEN)
            if s == True:
                fsmRecvFile = 2
                fileLen = int.from_bytes(d, byteorder='big')
                print("get file len: " + str(fileLen))
                SendCmd(CMD_NEXT_PKT)

        elif fsmRecvFile == 2:
            s, d = RecvCmdPacket(CMD_DATA_PKT)
            if s == True:
                if len(d) > 0:
                    payload = d
                    recvedFile.write(payload)
                    recvedFile.flush()
                    recvCnt += len(payload)
                    ShowProgress(recvCnt, fileLen)
                    SendCmd(CMD_NEXT_PKT)
                else:
                    fsmRecvFile = 3
                    SendCmd(CMD_NEXT_PKT)
        
        elif fsmRecvFile == 3:
            s, d = RecvCmdPacket(CMD_SEND_CPL)
            if s == True:
                fsmRecvFile = 0
                recvedFile.close()
                print("read file complete: " + str(fileLen) + " | " + str(recvCnt))
                
        else:
            print("Unknown cmd")


def RecvFile(recvFilePath):
    print("waiting packets...")
    recvedFile = open(recvFilePath, 'wb')
    recvCnt = 0
    fsmRecvFile = 0
    while not stop_event.is_set():
        s, d = RecvCmdPacket()
        if s == False:
            time.sleep(0.5)
            continue
        cmd = d[0]
        recvData = d[1:]
        
        if cmd == CMD_SEND_CPL:            
            fsmRecvFile = 0
            recvedFile.close()
            print("read file complete: " + str(fileLen) + " | " + str(recvCnt))

        elif cmd == CMD_NEW_FILE:
            recvedFile = open(recvFilePath, 'wb')
            SendCmd(CMD_NEXT_PKT)
            fsmRecvFile = 1

        elif cmd == CMD_FILE_LEN:
            if fsmRecvFile == 1:
                fileLen = int.from_bytes(recvData, byteorder='big')
                print("get file len: " + str(fileLen))
                SendCmd(CMD_NEXT_PKT)
                fsmRecvFile = 2
            elif fsmRecvFile == 2:
                fileLen = int.from_bytes(recvData, byteorder='big')
                print("get file len: " + str(fileLen))
                SendCmd(CMD_NEXT_PKT)
                fsmRecvFile = 3
            else:
                fsmRecvFile = 0
                print("unexpected file len packet")
        
        elif cmd == CMD_DATA_PKT:
            if len(recvData) > 0:
                payload = recvData
                recvedFile.write(payload)
                recvedFile.flush()
                recvCnt += len(payload)
                ShowProgress(recvCnt, fileLen)
                SendCmd(CMD_NEXT_PKT)
            else:
                fsmRecvFile = 0
                print("zero len data packet, mean no more data")

        else:
            print("Unknown cmd packet")
            fsmRecvFile = 0

        # if fsmRecvFile == 0:
        #     s, d = RecvCmdPacket(CMD_NEW_FILE)
        #     if s == True:
        #         recvedFile = open(recvFilePath, 'wb')
        #         SendCmd(CMD_NEXT_PKT)
        #         fsmRecvFile = 1

        # elif fsmRecvFile == 1:
        #     s, d = RecvCmdPacket(CMD_FILE_LEN)
        #     if s == True:
        #         fsmRecvFile = 2
        #         fileLen = int.from_bytes(d, byteorder='big')
        #         print("get file len: " + str(fileLen))
        #         SendCmd(CMD_NEXT_PKT)

        # elif fsmRecvFile == 2:
        #     s, d = RecvCmdPacket(CMD_DATA_PKT)
        #     if s == True:
        #         if len(d) > 0:
        #             payload = d
        #             recvedFile.write(payload)
        #             recvedFile.flush()
        #             recvCnt += len(payload)
        #             ShowProgress(recvCnt, fileLen)
        #             SendCmd(CMD_NEXT_PKT)
        #         else:
        #             fsmRecvFile = 3
        #             SendCmd(CMD_NEXT_PKT)
        
        # elif fsmRecvFile == 3:
        #     s, d = RecvCmdPacket(CMD_SEND_CPL)
        #     if s == True:
        #         fsmRecvFile = 0
        #         recvedFile.close()
        #         print("read file complete: " + str(fileLen) + " | " + str(recvCnt))
                
        # else:
        #     print("Unknown cmd")



def WriteFile(sendFile):
    PACKET_SIZE = 2000
    totalSize = os.path.getsize(sendFile)
    imageFile = open(sendFile, 'rb')
    print("totalSize: " + str(totalSize))
    sendCnt = 0
    SendCmd(CMD_NEW_FILE)
    if False == WaitCmd(CMD_NEXT_PKT):
        print("no receiver")
        sys.exit(400)

    SendCmd(CMD_FILE_LEN, totalSize.to_bytes(4, 'big'))
    if False == WaitCmd(CMD_NEXT_PKT):
        print("len sent, no receiver")
        sys.exit(401)

    print("ready to send file...")
    while not stop_event.is_set() and sendCnt < totalSize:
        packetData = imageFile.read(PACKET_SIZE)
        print("read file: " + str(len(packetData)), flush=True)
        if packetData == b'':
            print("EOF!" + str(sendCnt) + " " + str(totalSize), flush=True)
            break
        
        SendCmd(CMD_DATA_PKT, packetData)
        sendCnt += len(packetData)
        ShowProgress(sendCnt, totalSize)

        if False == WaitCmd(CMD_NEXT_PKT):  
            print("no receiver")
            sys.exit(400)
            
    imageFile.close()
    SendCmd(CMD_DATA_PKT)
    time.sleep(0.3)
    SendCmd(CMD_SEND_CPL)



if __name__ == '__main__':
    
    signal.signal(signal.SIGINT, interrupt_handler)
    parser = argparse.ArgumentParser(
        prog='liteflash',
        description='serial tool for debugging')

    parser.add_argument('--sf', type=str, default='None', help='file to be send')
    parser.add_argument('--rf', type=str, default='recv.bin', help='file name to be recv')
    parser.add_argument('--port', type=str, default='COM3', help='Serial port (default: COM9)')
    parser.add_argument('-baudrate', type=int, nargs='?', help='set baud rate, default: %(default)s', default=115200)
    args = parser.parse_args()

    sendFile = args.sf
    recvFile = args.rf
    portName = args.port
    baudrate = args.baudrate
    print("Using: " + str(portName) + " " + str(baudrate) + " "  + str(sendFile))

    ser = None
    try:
        ser = serial.Serial(portName, baudrate, timeout=0.5)
    except serial.SerialException as e:
        sys.stderr.write('Could not open serial port {}: {}\n'.format(portName, e))
        sys.exit(1)

    recv_thread = threading.Thread(target=ReadSerialHandler, args=(ser,), daemon=True)
    recv_thread.start()
    send_thread = threading.Thread(target=WriteSerialHandler, args=(ser,), daemon=True)
    send_thread.start()

    if sendFile == 'None':
        print("\r\nread mode")
        RecvFile(recvFile)
    else:
        print("\r\nwrite mode")
        WriteFile(sendFile)
        print("\r\nfile sent")
    
    while not stop_event.is_set():
        time.sleep(0.3)

        command = input(">>")
        if command == "1":
            SendCmd(ser, CMD_NEW_FILE)

        elif command == "2":
            sta, d = RecvCmdPacket()
            if sta == True:
                print("got cmd: " + str(d[0]))
            else:
                print("no cmd got")

        elif command == "3":
            DumpQueue()

        elif command == "0":
            # sys.exit(1)
            break

        else:
            print("无效的命令")


    ser.close()
