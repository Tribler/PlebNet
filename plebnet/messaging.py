import collections
import hashlib
import pickle
import random
import socket
import string
import threading
import time
from datetime import datetime

import rsa
from cryptography.fernet import Fernet

_ack = b'\xff'


def now():
    """
    Gets the current timestamp in seconds.
    :return: current timestamp as integer
    """
    return int(datetime.timestamp(datetime.now()))


def generate_contact_id(parent_id: str = ""):
    """
    Generates a random, virtually unique id for a new node.
    :param parent_id: id of the parent node, defaults to empty
    :return: the generated id
    """

    def generate_random_string(length):
        letters = string.ascii_lowercase
        return ''.join(random.choice(letters) for i in range(length))

    timestamp = str(now())

    random_seed = generate_random_string(5) + parent_id

    random_hash = hashlib.sha256(random_seed.encode('utf-8')).hexdigest()

    return random_hash + timestamp


class Contact:
    """
    Nodes contact.
    """

    def __init__(self, id: str, host: str, port: int, public_key: rsa.PublicKey, first_failure=None):
        """
        Instantiates a contact
        :param id: id of the node
        :param host: host of the node
        :param port: port of the node
        :param first_failure: first time of failure of communication with the node
        """

        self.id = id
        self.host = host
        self.port = port
        self.public_key = public_key
        self.first_failure = first_failure

    def link_down(self):
        """
        Sets the node link as down, by storing the current time as first_failure, if not set already.
        """

        if self.first_failure is None:
            self.first_failure = now()

    def link_up(self):
        """
        Sets the node link as up, by clearing the first_failure field
        """

        if self.first_failure is not None:
            self.first_failure = None

    def is_active(self):
        """
        Checks whether the contact is active.
        :return: true iff the contact is active
        """

        return self.first_failure is None


class MessageDeliveryError(Exception):

    def __init__(self, *args):

        if args:

            self.message = args[0]

        else:

            self.message = "Message delivery failed"


class MessageConsumer:

    def notify(self, message, sender_id):
        pass


class MessageSender:
    """
    Class for sending messages to MessageReceivers.
    """

    def __init__(self, receiver: Contact):
        """
        host: Receipient host name.
        port: Receipient port
        """
        self.receiver = receiver

        self._ack_length = len(_ack)

    def _build_packet(self, data, sender_id, private_key):
        """
        Builds packet header and payload to send.
        :param data: content of the message sent with the packet
        :param sender_id: id of the sender
        :param private_key: private key of the sender to sign the message
        :return: a tuple containing the packet header and the packet payload
        """

        symmetric_key, payload = self._build_encrypted_payload(data)

        header = self._build_header(payload, symmetric_key, sender_id, private_key)

        return header, payload

    def _build_encrypted_payload(self, data):
        """
        Encodes and encrypts symmetrically encrypts payload with a generated key.
        :param data: payload to encode (pickle) and
        :return: a tuple containing the symmetric key used and the encoded encrypted payload
        """

        pickled_data = pickle.dumps(data)

        symmetric_key = Fernet.generate_key()
        payload = Fernet(symmetric_key).encrypt(pickled_data)

        return symmetric_key, payload

    def _build_header(self, payload, symmetric_key, sender_id, private_key):
        """
        Builds a packet header
        :param payload: payload of the packet
        :param symmetric_key: symmetric key used to encrypt the payload
        :param sender_id: id of the sender of the message
        :param private_key: private key of the sender to sign the message
        :return: the built packet header
        """

        encrypted_symmetric_key = rsa.encrypt(symmetric_key, self.receiver.public_key)
        signature = rsa.sign(payload, private_key, 'SHA-1')

        variable = (str(len(payload)) + ':' + sender_id).encode('utf-8')

        return signature + encrypted_symmetric_key + variable

    def send_message(self, data, sender_contact_id, private_key, ack_timeout=5.0):
        """
        Sends a message.
        data: message payload
        """

        try:

            header, payload = self._build_packet(data, sender_contact_id, private_key)

            # Connecting to receiver
            s = socket.socket()
            s.connect((self.receiver.host, self.receiver.port))

            # Sending header
            s.send(header)
            s.settimeout(ack_timeout)
            s.recv(self._ack_length)

            # Sending payload
            s.send(payload)

            # Closing connection
            s.close()

        except:

            raise MessageDeliveryError()


class MessageReceiver:
    """
    Message receiving service. Opens a socket on the specified port, and notifies
    registered consumers when new messages are received.
    """

    """
    port: port to open listening socket on.
    connections_queue_size: size of the connections queue.
    notify_interval: (seconds) interval at which message consumers are notified.
    """

    def __init__(
            self,
            port: int,
            private_key: rsa.PrivateKey,
            contacts: list,
            connections_queue_size: int = 20,
            notify_interval: float = 1
    ):

        self.port = port
        self.private_key = private_key
        self.contacts = contacts
        self.connections_queue_size = connections_queue_size
        self.notify_interval = notify_interval

        self.messages_queue = collections.deque()

        self.message_consumers = []

        self.kill_flag = False

        threading.Thread(target=self.__start_listening).start()

        threading.Thread(target=self.__start_notifying).start()

    def register_consumer(self, message_consumer: MessageConsumer):
        """
        Registers a message_consumer.
        message_consumer: message_consumer to register as a listener
        """
        self.message_consumers.append(message_consumer)

    def __start_notifying(self):
        """
        Starts periodically checking the messages queue. When new messages are found, they are verified, decoded and
        forwarded to all registered consumers.
        """

        while True:

            if len(self.messages_queue) > 0:

                while len(self.messages_queue) > 0:
                    try:

                        signature, encrypted_payload_key, sender_id, payload = self.messages_queue.popleft()

                        # Verifying signature
                        rsa.verify(payload, signature, self.__get_contact_public_key(sender_id))

                        message = self._decode_payload(encrypted_payload_key, payload)

                        self.__notify_consumers(message, sender_id)

                    except:

                        continue

            time.sleep(self.notify_interval)

    def _decode_payload(self, encrypted_payload_key, payload):
        """
        Decrypts and decodes the payload, using the receiver's private key to decrypt the payload key.
        :param encrypted_payload_key: encrypted payload key, decrypted with the private key
        :param payload: encoded encrypted payload
        :return: decoded decrypted message
        """
        # Decrypting payload key
        payload_key = rsa.decrypt(encrypted_payload_key, self.private_key)

        # Decrypting and unpickling message
        pickled_message = Fernet(payload_key).decrypt(payload)
        message = pickle.loads(pickled_message)

        return message

    def __get_contact_public_key(self, contact_id: str):
        """
        Gets a known contact's public key. Throws exception if not found
        :param contact_id: id of the contact to retrieve the public key of
        :return: the retrieved public key
        """
        for contact in self.contacts:

            if contact.id == contact_id:
                return contact.public_key

        raise Exception("Contact not found " + contact_id)

    def kill(self):
        """
        Sets kill flag to true, effectively making the listening thread stop.
        """
        self.kill_flag = True

    def _initialize_socket(self):
        """
        Initializes a socket listening on the port specified at construction of the message receiver
        :return: the initialized socket
        """
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(('', self.port))
        s.listen(self.connections_queue_size)

        return s

    def __start_listening(self):
        """
        Starts listening for incoming connections.
        """

        s = self._initialize_socket()

        while not self.kill_flag:

            connection, addr = s.accept()

            if self.kill_flag:
                connection.close()
                break

            try:

                self._handle_connection(connection)

            except:

                continue

    def _handle_connection(self, connection):
        """
        Handles incoming connections to receive messages
        :param connection: connection to handle
        """

        header = connection.recv(256)

        signature, encrypted_payload_key, payload_length, sender_id = self._parse_header(header)

        connection.send(_ack)

        payload = connection.recv(payload_length)

        connection.close()

        self.messages_queue.append((signature, encrypted_payload_key, sender_id, payload))

    def _parse_header(self, header):
        """
        Parses a message header
        :param header: header to parse
        :return: a tuple containing message signature, the encrypted payload key, the payload length and the sender id
        """

        signature = header[:64]
        encrypted_payload_key = header[64:128]

        variable = header[128:].decode('utf-8')
        variable_parts = variable.split(':')

        payload_length = int(variable_parts[0])
        sender_id = variable_parts[1]

        return signature, encrypted_payload_key, payload_length, sender_id

    def __notify_consumers(self, message, sender_id):
        """
        Notifies all registered message consumers.
        """

        for consumer in self.message_consumers:
            consumer.notify(message, sender_id)


if __name__ == '__main__':
    sender_pub, sender_priv = rsa.newkeys(512)
    receiver_pub, receiver_priv = rsa.newkeys(512)

    receiver_contact = Contact(
        generate_contact_id(),
        '127.0.0.1',
        8000,
        receiver_pub
    )

    sender_contact = Contact(
        generate_contact_id(),
        '127.0.0.1',
        8001,
        sender_pub
    )

    receiver = MessageReceiver(
        receiver_contact.port,
        receiver_priv,
        [sender_contact]
    )


    class Printer(MessageConsumer):

        def notify(self, message, sender_id):
            print("Received message: " + message)


    receiver.register_consumer(Printer())

    sender = MessageSender(receiver_contact)

    sender.send_message("Ciao come stai?", sender_contact.id, sender_priv)
