class RoundRobinQueue(object):
    def __init__(self):
        # Queue elements:
        #   1. round_robin_queue = maintains an ordered list of senders. Used to determine which
        #      sender is being serviced.
        #   2. send_queues = every sender has its own key in this list. Key maps to an ordered list
        #      of all requests by a single sender.
        self.round_robin_order = []
        self.sender_queues = {}

    def __len__(self):
        length = 0
        if len(self.round_robin_order):
            for sender in self.round_robin_order:
                length += len(self.sender_queues[sender])
        return length

    def push(self, sender, item):
        if sender not in self.round_robin_order:
            self.round_robin_order.append(sender)
        if sender not in self.sender_queues:
            self.sender_queues[sender] = []
        self.sender_queues[sender].append(item)

    def pop(self):
        result = None
        if len(self.round_robin_order):
            sender = self.round_robin_order.pop(0)
            if sender in self.sender_queues:
                if len(self.sender_queues[sender]):
                    result = self.sender_queues[sender].pop(0)
                    # There may still be items left to print for this particular sender. If so,
                    # will need to place sender back into round robin rotation.
                    if len(self.sender_queues[sender]):
                        self.round_robin_order.append(sender)
                    # If no items remaining, can remove sender key from send queues.
                    else:
                        self.sender_queues.pop(sender, None)
        return result

    def peek(self):
        result = None
        if len(self.round_robin_order):
            sender = self.round_robin_order[0]
            if sender in self.sender_queues:
                if len(self.sender_queues[sender]):
                    result = self.sender_queues[sender][0]
        return result

