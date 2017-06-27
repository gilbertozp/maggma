import sys
import logging
import multiprocessing
import queue
from collections import defaultdict
from itertools import cycle

from monty.json import MSONable

logger = logging.getLogger(__name__)
sh = logging.StreamHandler(stream=sys.stdout)
sh.setLevel(logging.DEBUG)
sh.setFormatter('%(asctime)s %(levelname)s %(message)s')
logger.addHandler(sh)


class Runner(MSONable):

    def __init__(self, builders, use_mpi=True, num_workers=1):
        """
        Initialize with a lit of builders

        Args:
            builders(list): list of builders
            use_mpi (bool): if True its is assumed that the building is done via MPI, else
                multiprocessing is used.
            num_workers (int): number of processes. Used only for multiprocessing.
        """
        self.builders = builders
        self.use_mpi = use_mpi
        self.num_workers = num_workers if not use_mpi else 1
        # multiprocessing only if mpi is not used, no mixing
        if not use_mpi:
            if self.num_workers > 1:
                self._queue = multiprocessing.Queue()
                manager = multiprocessing.Manager()
                self.processed_items = manager.dict()
            # serial
            else:
                self._queue = queue.Queue()
                self.processed_items = dict()
        else:
            try:
                from mpi4py import MPI
            except ImportError:
                raise ImportError("Trying to use MPI without 'mpi4py' package. Refuse to proceed "
                                  "with this insanity!")
        self.dependency_graph = self._get_builder_dependency_graph()
        self.has_run = []  # for bookkeeping builder runs
        self.status = []  # builder run status

    # TODO: make it efficient, O(N^2) complexity at the moment,
    # might be ok(not many builders)? - KM
    def _get_builder_dependency_graph(self):
        """
        Does the following:
        1.) use targets and sources of builders to determine interdependencies
        2.) order builders according to interdependencies

        Returns:
            dict
        """
        # key = index of the builder in the self.builders list
        # value = list of indices of builders that the key depends on i.e these must run before
        # the builder corresponding to the key.
        links_dict = defaultdict(list)
        for i, bi in enumerate(self.builders):
            for j, bj in enumerate(self.builders):
                if i != j:
                    for s in bi.sources:
                        if s in bj.targets:
                            links_dict[i].append(j)
        return links_dict

    def run(self):
        """
        Does the following:
            - traverse through the builder dependency graph and does the following to
              each builder
                - connect to sources
                - get items and feed it to the processing pipeline
                - process each item
                    - supported options: serial, MPI or the builtin multiprocessing
                - collect all processed items
                - connect to the targets
                - update targets
                - finalize aka cleanup(close all connections etc)
        """
        for i, b in enumerate(self.builders):
            self._build_dependencies(i)
    
    def _build_dependencies(self, builder_id):
        """
        Run the builders by recursively traversing through the dependency graph.

        Args:
            builder_id (int): builder index
        """
        if builder_id in self.has_run:
            return
        else:
            if self.dependency_graph[builder_id]:
                for j in self.dependency_graph[builder_id]:
                    self._build_dependencies(j)
            self._run_builder(builder_id)
            self.has_run.append(builder_id)

    def _run_builder(self, builder_id):
        """
        Run builder, self.builders[builder_id]

        Args:
            builder_id (int): builder index

        Returns:

        """
        if self.use_mpi:
            logger.info("building: ", builder_id)
            self._run_builder_in_mpi(builder_id)
        else:
            self._run_builder_in_multiproc(builder_id)

    def _run_builder_in_mpi(self, builder_id):
        """

        Args:
            builder_id (int): the index of the builder in the builders list
        """
        (comm, rank, size) = get_mpi()
        processed_items_dict = {}

        # master: doesnt do any 'work', just distributes the workload.
        if rank == 0:
            builder = self.builders[builder_id]
            # establish connection to the sources
            builder.connect(sources=True)

            # cycle through the workers, there could be less workers than the items to process
            worker_id = cycle(range(1, size))

            n = 0
            # distribute the items to process
            for item in builder.get_items():
                packet = (builder_id, item)
                comm.send(packet, dest=next(worker_id))
                n = n+1

            logger.info("{} items sent for processing".format(n))

            # get processed item from the workers
            for i in range(n):
                try:
                    processed_item = comm.recv()
                    self.status.append(True)
                    processed_items_dict.update(processed_item)
                except:
                    raise

            # kill workers
            for _ in range(size - 1):
                comm.send(None, dest=next(worker_id))

            # update the targets
            builder.connect(sources=False)
            builder.update_targets(processed_items_dict)

            if all(self.status):
                builder.finalize()

        # workers:
        #   - process item
        #   - update target
        #   - report status
        else:
            self.worker(comm)

    def _run_builder_in_multiproc(self, builder_id):
        """
        Adapted from pymatgen-db

        Args:
            builder_id (int): the index of the builder in the builders list
        """
        processes = []
        builder = self.builders[builder_id]

        # establish connection to the sources
        builder.connect(sources=True)

        # send items to process
        for item in builder.get_items():
            packet = (builder_id, item)
            self._queue.put(packet)

        # start the workers
        if self.num_workers > 1:
            for i in range(self.num_workers):
                print("starting")
                proc = multiprocessing.Process(target=self.worker, args=(None,))
                proc.start()
                processes.append(proc)

            # get job status from the workers
            for i in range(self.num_workers):
                processes[i].join()
                code = processes[i].exitcode
                self.status.append(not bool(code))
        else:
            try:
                self.worker()
                self.status.append(True)
            except:
                raise

        # update the targets
        builder.connect(sources=False)
        builder.update_targets(self.processed_items)

        if all(self.status):
            self.builders[builder_id].finalize()

    def worker(self, comm=None):
        """
        Where shit gets done!
        Call the builder's process_item method and send back(or put it in the shared dict) the
        processed item

        Args:
            comm (MPI.comm): mpi communicator
        """

        if self.use_mpi:
            while True:
                packet = comm.recv(source=0)
                if packet is None:
                    break
                builder_id, item = packet
                processed_item = self.builders[builder_id].process_item(item)
                comm.ssend(processed_item, 0)
        else:
            while True:
                try:
                    packet = self._queue.get(timeout=2)
                    builder_id, item = packet
                    processed_item = self.builders[builder_id].process_item(item)
                    self.processed_items.update(processed_item)
                except queue.Empty:
                    break

    # TODO: scrape this? - KM
    def _run_builder_in_mpi_collective_comm(self, builder, scatter=True):
        """
        Since all the items to be processed are fetched on the master node at once, this
        implementation could be problematic if there are large number of items or small number of
        large items.

        At the moment it is hard to get around this: only pickleable objects can be passed
        around using MPI and generators/Queues(uses thread locking internally) are not pickleable!!

        Args:
            builder (Builder): Any object of class that subclasses Builder
            scatter (bool): if True then the items are scattered from the master to slaves, else
                broadcasted.
        """

        (comm, rank, size) = get_mpi()

        items = None

        # get all items at the master
        if rank == 0:
            items = list(builder.get_items())

        # pad items if necessary and scatter it to the slaves
        # ==>
        # large memory consumption(if the number and/or size of the items are large) ONLY at the master
        if scatter:
            itm = None
            if rank == 0:
                n = len(items)
                chunk_size, n_chunks = (n//size, size) if size <= n else (1, n)
                itm = [items[r * chunk_size:(r + 1) * chunk_size] for r in range(n_chunks)]
                # if there are processes than elements, pad the scattering list
                itm.extend([[] for _ in range(max(0, size - n))])
                if 0 < n % size < n:
                    itm[-1].extend(items[size * chunk_size:])
                # print("size", size, chunk_size)

            itm = comm.scatter(itm, root=0)

            builder.process_item(itm)

        # broadcast all items from the master to the slaves.
        # ==>
        # large memory consumption(if the number and/or size of the items are large) on ALL NODES.
        else:
            items = comm.bcast(items, root=0) if comm else items

            n = len(items)
            chunk_size = n // size

            # adjust chuck size if the data size is not divisible by the
            # number of processors
            if rank == 0:
                if n % size != 0:
                    chunk_size = chunk_size + n % size

            items_chunk = items[rank:rank + chunk_size]

            for itm in items_chunk:
                builder.process_item(itm)


def get_mpi():
    try:
        from mpi4py import MPI

        comm = MPI.COMM_WORLD
        rank = comm.Get_rank()
        size = comm.Get_size()
    except ImportError:
        comm = None
        rank = 0
        size = 1
        logger.warning("No MPI. Install mpi4py package")

    return comm, rank, size

