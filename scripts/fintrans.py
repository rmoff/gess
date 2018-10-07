#!/usr/bin/python

""" 
  The implementation of the synthetic financial transaction stream,
  used in the main script of gess.

@author: Michael Hausenblas, http://mhausenblas.info/#i
@since: 2013-11-08
@status: init
"""
import sys
import os
import socket
import logging
import string
import datetime
import random
import uuid
import csv
import json
from time import sleep

DEBUG = False

# defines the host for a single gess running
GESS_IP = "127.0.0.1"

# defines default port for a single gess running
GESS_UDP_PORT = 6900

# defines delay (seconds) to inject between events
DELAY = 1

# defines the sampling interval (in seconds) for reporting runtime statistics
SAMPLE_INTERVAL = 10

# lower range for randomly emitted frauds (min. tick between trans)
FRAUD_TICK_MIN = 5

# upper range for randomly emitted frauds (max. tick between trans)
FRAUD_TICK_MAX = 15

# ATM withdrawal data config
AMOUNTS = [20, 50, 100, 200, 300, 400]


if DEBUG:
  FORMAT = '%(asctime)-0s %(levelname)s %(message)s [at line %(lineno)d]'
  logging.basicConfig(level=logging.DEBUG, format=FORMAT, datefmt='%Y-%m-%dT%I:%M:%S')
else:
  FORMAT = '%(message)s'
  logging.basicConfig(level=logging.INFO, format=FORMAT)


class FinTransSource(object):

  def __init__(self, atm_loc_sources):
         #threading.Thread.__init__(self)
         self.send_port = GESS_UDP_PORT
         self.atm_loc = {}
         for atm_loc in atm_loc_sources:
           self._load_data(atm_loc)
  
  # loads the ATM location data from the specified CSV data file
  def _load_data(self, atm_loc_data_file):
    logging.debug('Trying to parse ATM location data file %s' %(atm_loc_data_file))
    osm_atm_file = open(atm_loc_data_file, 'rb')
    atm_counter = 0
    try:
      reader = csv.reader(osm_atm_file, delimiter=',')
      for row in reader:
        lat, lon, atm_label = row[1], row[0], row[2] 
        atm_counter += 1
        self.atm_loc[str(atm_counter)] = lat, lon, atm_label
        logging.debug(' -> loaded ATM location %s, %s' %(lat, lon))
    finally:
      osm_atm_file.close()
      logging.debug(' -> loaded %d ATM locations in total.' %(atm_counter))
  
  # creates a single financial transaction (ATM withdrawal) using the following
  # format:
  # {
  #     "account_id": "a228",
  #     "timestamp": "2018-10-05T10:47:54.189407",
  #     "atm": "Bank of the West",
  #     "amount": 300,
  #     "location": {
  #         "lat": "37.2500148",
  #         "lon": "-121.8621345"
  #     },
  #     "transaction_id": "bb35284a-c883-11e8-8421-186590d22a35"
  # }
  #
  # NB timezone is hardcoded to UTC. Sorry. 
  def _create_fintran(self):
    rloc = random.choice(self.atm_loc.keys()) # obtain a random ATM location
    lat, lon, atm_label = self.atm_loc[rloc]
    fintran = {
      'timestamp' : str(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S %z+0000")),
      'atm' : str(atm_label),
      'location' : {
        'lat' : str(lat),
        'lon' :  str(lon)
      },
      'amount' : random.choice(AMOUNTS),
      'account_id' : 'a' + str(random.randint(1, 1000)),
      'transaction_id' : str(uuid.uuid1())
    }    
    logging.debug('Created financial transaction: %s' %fintran)
    return (fintran, sys.getsizeof(str(fintran)))

  # creates a single fraudulent financial transaction (ATM withdrawal)
  # based on an existing transaction, using the following format:
  # {
  #   "account_id": "a314",
  #   "timestamp": "2018-10-05T10:48:07.722905",
  #   "atm": "Bank of America",
  #   "amount": 50,
  #   "location": {
  #       "lat": "37.2715281",
  #       "lon": "-121.9067962"
  #   },
  #   "transaction_id": "xxxc34630ba-c883-11e8-8dc3-186590d22a35"
  # }
  # Note: the fraudulent transaction will have the same account ID as
  #       the original transaction but different location and amount.
  # The timestamp will be randomly different, in a range between one minute and ten minutes earlier than the 'real' txn.
  #
  # NB timezone is hardcoded to UTC. Sorry. 

  def _create_fraudtran(self, fintran):
    rloc = random.choice(self.atm_loc.keys()) # obtain a random ATM location
    lat, lon, atm_label = self.atm_loc[rloc]
    fraudtran = {
      'timestamp' : str((datetime.datetime.now() - datetime.timedelta(seconds=random.randint(60,600))).strftime("%Y-%m-%d %H:%M:%S %z+0000")),
      'atm' : str(atm_label),
      'location' : {
        'lat' : str(lat),
        'lon' :  str(lon)
      },
      'amount' : random.choice(AMOUNTS),
      'account_id' : fintran['account_id'],
      'transaction_id' : 'xxx' + str(fintran['transaction_id'])
    }    
    logging.debug('Created fraudulent financial transaction: %s' %fraudtran)
    return (fraudtran, sys.getsizeof(str(fraudtran)))

  # sends a single financial transaction via UDP
  def _send_fintran(self, out_socket, fintran):
    out_socket.sendto(str(fintran) + '\n', (GESS_IP, self.send_port))
    logging.debug('Sent financial transaction: %s' %fintran)
    
  ############# API ############################################################

  # dumps the OSM ATM data
  def dump_data(self):
    for k, v in self.atm_loc.iteritems():
      logging.info('ATM %s location: %s %s' %(k, v[0], v[1])) 
  
  # generates financial transactions (ATM withdrawals) and sends them
  # via UDP on port GESS_UDP_PORT as well as logs runtime statistics.
  def run(self):
    out_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) # use UDP
    start_time = datetime.datetime.now()
    num_fintrans = 0
    tp_fintrans = 0
    num_bytes = 0
    tp_bytes = 0
    ticks = 0 # ticks (virtual time basis for emits)
    fraud_tick = random.randint(FRAUD_TICK_MIN, FRAUD_TICK_MAX) 
  
    # the header of the TSV-formatted log statistics file
    # (all values are relative to the sample interval)
    #  timestamp
    #  num_fintrans ... financial transactions emitted (in thousands)
    #  tp_fintrans ... throughput of financial transactions (in thousands/sec)
    #  num_bytes ... number of bytes emitted (in MB)
    #  tp_bytes ... throughput of bytes (in MB/sec)
    logging.info('timestamp\tnum_fintrans\ttp_fintrans\tnum_bytes\ttp_bytes')
    
    while True:
      
      ticks += 1      
      logging.debug('TICKS: %d' %ticks)

      (fintran, fintransize) = self._create_fintran()
      self._send_fintran(out_socket, json.dumps(fintran))

      sleep(DELAY)

      # here a fraudulent transaction will be ingested, randomly every 10-100sec
      if ticks > fraud_tick:
        (fraudtran, fraudtransize) = self._create_fraudtran(fintran)
        self._send_fintran(out_socket, json.dumps(fraudtran))
        num_fintrans += 2
        num_bytes += fintransize + fraudtransize
        ticks = 0
        fraud_tick = random.randint(FRAUD_TICK_MIN, FRAUD_TICK_MAX)
      else:  
        num_fintrans += 1
        num_bytes += fintransize
  
      end_time = datetime.datetime.now()
      diff_time = end_time - start_time
    
      if diff_time.seconds > (SAMPLE_INTERVAL - 1):
        tp_fintrans = (num_fintrans/1000) / diff_time.seconds
        tp_bytes = (num_bytes/1024/1024) / diff_time.seconds
        logging.info('%s\t%d\t%d\t%d\t%d'
          %(
            str(datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')),
            (num_fintrans/1000), 
            tp_fintrans,
            (num_bytes/1024/1024),
            tp_bytes
          )
        )
        start_time = datetime.datetime.now()
        num_fintrans = 0
        num_bytes = 0
