import rclpy
from rclpy.node import Node
from enum import Enum

from autonav_msgs.msg import ConBusInstruction, Log, DeviceState, SystemState


class Device(Enum):
    STEAM_TRANSLATOR = 100
    MANUAL_CONTROL = 101
    DISPLAY_NODE = 102
    SERIAL_IMU = 103
    SERIAL_CAN = 104
    LOGGING = 105


class DeviceStateEnum(Enum):
    OFF = 1
    STANDBY = 2
    READY = 3
    OPERATING = 4
    UNKNOWN = 5


class SystemStateEnum(Enum):
    DISABLED = 1
    AUTONOMOUS = 2
    MANUAL = 3


class ConbusOpcode(Enum):
    READ = 0,
    READ_ACK = 1
    WRITE = 2
    WRITE_ACK = 3
    READ_ALL = 4


class LogLevel(Enum):
    DEBUG = 0
    INFO = 1
    WARNING = 2
    ERROR = 3
    CRITICAL = 4
    

class AddressType(Enum):
    INTEGER = 0
    FLOAT = 1
    BOOL = 2


FLOAT_PRECISION = 10000000.0
MAX_DEVICE_ID = 200


class Conbus:
    def __init__(self, device: Device, node: Node):
        self.device = device
        self.registers = {}

        self.publisher = node.create_publisher(
            ConBusInstruction, "/autonav/conbus", 10)
        self.subscriber = node.create_subscription(
            ConBusInstruction, "/autonav/conbus", self.on_conbus_instruction, 10)

    def intToBytes(self, data: int):
        byts = data.to_bytes(4, byteorder="big", signed=True)
        byts = bytes([AddressType.INTEGER.value, 0]) + byts
        return byts

    def floatToBytes(self, data: float):
        data = int(data * FLOAT_PRECISION)
        byts = self.intToBytes(data)
        byts = bytes([AddressType.FLOAT.value, 0]) + byts[2:]
        return byts

    def boolToBytes(self, data: bool):
        if data:
            return bytes([AddressType.BOOL.value, 0, 1])
        else:
            return bytes([AddressType.BOOL.value, 0, 0])

    def write(self, address: int, data: bytes, dontPublish=False):
        if self.device.value not in self.registers:
            self.registers[self.device.value] = {}
        self.registers[self.device.value][address] = data

        if not dontPublish:
            msg = ConBusInstruction()
            msg.device = self.device.value
            msg.address = address
            msg.data = data
            msg.opcode = ConbusOpcode.WRITE_ACK.value
            self.publisher.publish(msg)
            
    def writeFloat(self, address: int, data: float, dontPublish=False):
        self.write(address, self.floatToBytes(data), dontPublish)
        
    def writeInt(self, address: int, data: int, dontPublish=False):
        self.write(address, self.intToBytes(data), dontPublish)

    def writeBool(self, address: int, data: bool, dontPublish=False):
        self.write(address, self.boolToBytes(data), dontPublish)

    def readBytes(self, address: int):
        if self.device.value not in self.registers:
            self.registers[self.device.value] = {}
            
        if address not in self.registers[self.device.value]:
            return None
            
        return self.registers[self.device.value][address]

    def readInt(self, address: int):
        byts = self.readBytes(address)
        
        if byts is None:
            return None

        if len(byts) == 6:
            # Convert last 4 bytes from big endian to int
            return int.from_bytes(byts[2:], byteorder="big", signed=True)

        raise Exception("Invalid integer size")

    def readBool(self, address: int):
        return bool(self.readInt(address))

    def readFloat(self, address: int):
        return self.readInt(address) / FLOAT_PRECISION

    def writeTo(self, device: Device, address: int, data: bytes):
        msg = ConBusInstruction()
        msg.device = device
        msg.address = address
        msg.data = data
        msg.opcode = ConbusOpcode.WRITE
        self.publisher.publish(msg)

    def on_conbus_instruction(self, instruction: ConBusInstruction):
        data = self.readBytes(instruction.address)
        if instruction.opcode == ConbusOpcode.READ.value and instruction.device == self.device.value:
            if data is None:
                return
            msg = ConBusInstruction()
            msg.device = self.device.value
            msg.address = instruction.address
            msg.data = data
            msg.opcode = ConbusOpcode.READ_ACK.value
            self.publisher.publish(msg)

        if instruction.opcode == ConbusOpcode.READ_ACK.value:
            if instruction.device not in self.registers:
                self.registers[instruction.device] = {}

            self.registers[instruction.device][instruction.address] = instruction.data

        if instruction.opcode == ConbusOpcode.WRITE.value and instruction.device == self.device.value:
            self.write(instruction.address, instruction.data)

        if instruction.opcode == ConbusOpcode.WRITE_ACK.value:
            if instruction.device not in self.registers:
                self.registers[instruction.device] = {}

            self.registers[instruction.device][instruction.address] = instruction.data

        if instruction.opcode == ConbusOpcode.READ_ALL.value and instruction.device == self.device.value:
            if self.device.value not in self.registers:
                return

            for key in self.registers[self.device.value]:
                msg = ConBusInstruction()
                msg.device = self.device.value
                msg.address = key
                msg.data = self.registers[self.device.value][key]
                msg.opcode = ConbusOpcode.READ_ACK.value
                self.publisher.publish(msg)


class AutoNode(Node):
    def __init__(self, device: Device, node_name):
        super().__init__(node_name)

        self.config = Conbus(device, self)
        self.device = device
        self.log_publisher = self.create_publisher(Log, "/autonav/logging", 10)
        self.device_state_publisher = self.create_publisher(DeviceState, "/autonav/state/device", 10)
        self.device_state_subscriber = self.create_subscription(DeviceState, "/autonav/state/device", self.on_device_state, 10)
        self.system_state_publisher = self.create_publisher(SystemState, "/autonav/state/system", 10)
        self.system_state_subscriber = self.create_subscription(SystemState, "/autonav/state/system", self.on_system_state, 10)
        self.deviceStates = {}
        self.system_state = SystemState()
        self.system_state.estop = False
        self.system_state.state = SystemStateEnum.DISABLED.value
        
        self.set_state(DeviceStateEnum.STANDBY)
        
    def on_system_state(self, state: SystemState):
        self.state = state
        
    def set_state(self, state: DeviceStateEnum):
        self.state = state
        msg = DeviceState()
        msg.device = self.device.value
        msg.state = state.value
        self.deviceStates[msg.device] = msg.state
        self.device_state_publisher.publish(msg)
        
    def on_device_state(self, state: DeviceState):
        self.deviceStates[state.device] = state.state
        
    def set_system_state(self, state: SystemStateEnum, estop = None):
        msg = SystemState()
        msg.state = state.value
        if estop or self.system_state.estop:
            msg.estop = True
        else:
            msg.estop = False
        self.system_state = msg
        self.system_state_publisher.publish(msg)

    def log(self, message: str, level: LogLevel = LogLevel.INFO, file: str = None, skipFile=False, skipConsole=False):
        if not skipFile:
            if file is None:
                file = self.get_name()

            msg = Log()
            msg.data = message
            msg.file = file
            self.log_publisher.publish(msg)

        if not skipConsole:
            if level == LogLevel.DEBUG:
                self.get_logger().debug(message)
            if level == LogLevel.INFO:
                self.get_logger().info(message)
            if level == LogLevel.WARNING:
                self.get_logger().warn(message)
            if level == LogLevel.ERROR:
                self.get_logger().error(message)
            if level == LogLevel.CRITICAL:
                self.get_logger().error(message)
