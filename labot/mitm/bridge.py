"""
Classes to take decisions for transmitting the packets

The classes inheriting from BridgeHandler must
implement `handle`.

The classes inheriting from MsgBridgeHandler must
implement `handle_message`.
"""

import select
from abc import ABC, abstractmethod
from collections import deque
import os
import logging
import time
import requests
from ..data import Buffer, Msg, Dumper
from .. import protocol

logger = logging.getLogger("labot")
# TODO: use the logger


def from_client(origin):
    return origin.getpeername()[0] == "127.0.0.1"


def direction(origin):
    if from_client(origin):
        return "Client->Server"
    else:
        return "Server->Client"


class BridgeHandler(ABC):
    """Abstract class for bridging policies.
    You just have to subclass and fill the handle method.
    
    It implements the proxy_callback that will be called
    when a client tries to connect to the server.
    proxy_callback will call `handle` on every packet.

    To modify the behavior, you have to create subclasses pf
    BridgeHandler.
    """

    def __init__(self, coJeu, coSer):
        self.coJeu = coJeu
        self.coSer = coSer
        self.other = {coJeu: coSer, coSer: coJeu}
        self.conns = [coJeu, coSer]

    @abstractmethod
    def handle(self, data, origin):
        pass

    @classmethod
    def proxy_callback(cls, coJeu, coSer):
        """Callback that can be called by the proxy

        It creates an instance of the class and
        calls `handle` on every packet

        coJeu: socket to the game
        coSer: socket to the server
        """
        bridge_handler = cls(coJeu, coSer)
        bridge_handler.loop()

    def loop(self):
        conns = self.conns
        active = True
        try:
            while active:
                rlist, wlist, xlist = select.select(conns, [], conns)
                if xlist or not rlist:
                    break
                for r in rlist:
                    data = r.recv(8192)
                    if not data:
                        active = False
                        break
                    self.handle(data, origin=r)
        finally:
            for c in conns:
                c.close()


class DummyBridgeHandler(BridgeHandler):
    """Implements a dummy policy
    that forwards all packets"""

    def handle(self, data, origin):
        self.other[origin].sendall(data)


class PrintingBridgeHandler(DummyBridgeHandler):
    """
    Implements a dummy policy that
    forwards and prints all packets
    """

    def handle(self, data, origin):
        super().handle(data, origin)
        print(direction(origin), data.hex())


class MsgBridgeHandler(DummyBridgeHandler, ABC):
    """
    Advanced policy to work with the parsed messages
    instead of the raw packets like BridgeHandler.
    
    This class implements a generic `handle` that calls 
    `handle_message` which acts on the parsed messages
    and that should be implemented by the subclasses.
    """

    def __init__(self, coJeu, coSer):
        super().__init__(coJeu, coSer)
        self.buf = {coJeu: Buffer(), coSer: Buffer()}

    def handle(self, data, origin):

        super().handle(data, origin)
        self.buf[origin] += data
        from_client = origin == self.coJeu
        # print(direction(origin), self.buf[origin].data)
        msg = Msg.fromRaw(self.buf[origin], from_client)
        while msg is not None:
            if msg.id in protocol.msg_from_id:
                msgType = protocol.msg_from_id[msg.id]
                parsedMsg = protocol.read(msgType, msg.data)
                assert msg.data.remaining() == 0, (
                    "All content of %s have not been read into %s:\n %s"
                    % (msgType, parsedMsg, msg.data)
                )
                self.handle_message(parsedMsg, origin)
            else:
                print('sorry, no '+str(msg.id))
            #msgType = protocol.msg_from_id[msg.id]
#            parsedMsg = protocol.read(msgType, msg.data)

 ##           assert msg.data.remaining() == 0, (
   #             "All content of %s have not been read into %s:\n %s"
    #            % (msgType, parsedMsg, msg.data)
     #       )

      #      self.handle_message(parsedMsg, origin)
            msg = Msg.fromRaw(self.buf[origin], from_client)

    @abstractmethod
    def handle_message(self, msg, origin):
        pass


class PrintingMsgBridgeHandler(MsgBridgeHandler):
    def handle_message(self, msg, origin):
        print(direction(origin))
        print(msg)
        print()
        print()


class InjectorBridgeHandler(BridgeHandler):
    """Forwards all packets and allows to inject
    packets
    """

    def __init__(self, coJeu, coSer, db_size=100, dumper=None):
        super().__init__(coJeu, coSer)
        self.buf = {coJeu: Buffer(), coSer: Buffer()}
        self.injected_to_client = 0
        self.injected_to_server = 0
        self.counter = 0
        self.db = deque([], maxlen=db_size)
        self.dumper = dumper

    def send_to_client(self, data):
        if isinstance(data, Msg):
            data = data.bytes()
        self.injected_to_client += 1
        self.coJeu.sendall(data)

    def send_to_server(self, data):
        if isinstance(data, Msg):
            data.count = self.counter + 1
            data = data.bytes()
        self.injected_to_server += 1
        self.coSer.sendall(data)

    def ask_item_price(self, itemid=11971):
        msg= Msg.from_json(
            {"__type__": "ExchangeBidHouseSearchMessage", "follow": True, 'genId': itemid}
        )
        self.send_to_server(msg)

    def send_message(self, s):
        msg = Msg.from_json(
            {"__type__": "ChatClientMultiMessage", "content": s, "channel": 0}
        )
        self.send_to_server(msg)


    def handle(self, data, origin):
        self.other[origin].sendall(data) # ici
        self.buf[origin] += data
        from_client = origin == self.coJeu

        msg = Msg.fromRaw(self.buf[origin], from_client)

        while msg is not None:
            if msg.id in protocol.msg_from_id:
                msgType = protocol.msg_from_id[msg.id]
                parsedMsg = protocol.read(msgType, msg.data)

                assert msg.data.remaining() in [0, 48], (
                    "All content of %s have not been read into %s:\n %s"
                    % (msgType, parsedMsg, msg.data)
                )
                if from_client:
                    logger.debug(
                        ("-> [%(count)i] %(name)s (%(size)i Bytes)"),
                        dict(
                            count=msg.count,
                            name=protocol.msg_from_id[msg.id]["name"],
                            size=len(msg.data),
                        ),
                    )
                else:
                    logger.debug(
                        ("<- %(name)s (%(size)i Bytes)"),
                        dict(name=protocol.msg_from_id[msg.id]["name"], size=len(msg.data)),
                    )
                if from_client:
                    msg.count += self.injected_to_server - self.injected_to_client
                    self.counter = msg.count
                else:
                    self.counter += 1
                self.db.append(msg)
                if self.dumper is not None:
                    self.dumper.dump(msg)
#ici                self.other[origin].sendall(msg.bytes()) #self.other[origin].sendall(msg.bytes()) data
                self.handle_message(parsedMsg, origin)
                msg = Msg.fromRaw(self.buf[origin], from_client)
                time.sleep(0.5) #0.005
            else:
#ici                self.other[origin].sendall(data)
                print(str(data)+ 'sent !!!!')
                time.sleep(0.5)

#                print('sorry, no pkt id in db '+str(msg.id))
#                logger.debug('sorry, no pkt id in db '+str(msg.id))
#                if from_client:
#                    msg.count += self.injected_to_server - self.injected_to_client
#                    self.counter = msg.count
#                else:
#                    self.counter += 1
#                self.other[origin].sendall(data)
#                #msg = Msg.fromRaw(self.buf[origin], from_client)
#                time.sleep(0.5)


    def handle_message(self, m, o):
        print(direction(o))
        print(m)
        print()
        print()
        pass


class LucInjector(DummyBridgeHandler):

    def __init__(self, coJeu, coSer):
        super().__init__(coJeu, coSer)
        self.buf = {coJeu: Buffer(), coSer: Buffer()}
        self.injected_to_server = 0
        self.counter = 0
        self.injections = 0
        self.script = "off"
        self.items = [16663,17123,17124,17125,17126,17127,17128,17129,17130,17131,17132,17133,17134,17135,17136,17137,17138,17139,17140,17141,17142,17143,17144,17145,17146,17147,17148,17149,17150,17151,17152,17153,17154,17155,17156,17157,17158,17159,17160,17161,17421,372,485,756,998,1020,1691,2299,2480,2560,2562,2646,3000,3001,3002,3208,6475,6843,6845,8086,8757,8758,8760,8789,8804,8809,11281,11696,12739,12742,13098,13172,13496,13699,13738,13740,13742,13743,13744,13745,13747,13914,13936,13947,13948,14143,14466,14471,14517,14518,14861,14863,14922,14923,14924,14925,14926,15052,15164,15445,15448,15449,15458,15462,15478,15724,15811,16152,16157,17617,17623,17624,18363,19408,19410,19969,19970,19973,20911,20913,21209,21211,1686,1687,1688,1689,1692,10031,2539,2540,2543,12733,12744,12745,16212,16458,16459,16460,12582,12583,12584,12585,12586,12587,16909,16910,16911,16912,16913,16914,16915,16916,16917,16918,16919,16920,16921,16922,16923,16924,16925,16926,276,292,311,373,385,386,406,410,417,420,429,440,477,501,641,642,643,752,792,838,1018,1023,1086,1089,1328,1464,1465,1466,1467,1506,1507,1508,1509,1510,1676,1683,1684,1731,1732,1733,1749,1770,1771,1773,1775,1777,1977,1983,1985,2012,2058,2059,2246,2266,2267,2268,2274,2280,2286,2294,2295,2296,2297,2302,2316,2320,2321,2328,2330,2401,2448,2449,2454,2455,2462,2466,2467,2468,2479,2481,2482,2484,2486,2488,2492,2493,2494,2499,2500,2504,2505,2506,2508,2510,2515,2525,2526,2527,2549,2553,2556,2558,2561,2566,2572,2575,2576,2577,2582,2584,2585,2596,2607,2617,2618,2619,2620,2622,2623,2625,2626,2627,2628,2632,2645,2648,2649,2650,2651,2652,2653,2656,2662,2663,2669,2676,2805,2806,3209,6476,6478,6479,6480,6622,6625,6626,6648,6649,6650,6651,6652,6659,6770,6885,6904,7220,7386,7919,8001,8064,8075,8076,8077,8137,8140,8144,8160,8326,8364,8396,8516,8517,8671,8672,8673,8674,8675,8759,8765,8783,8784,8787,8788,8790,8805,8806,8807,8810,8811,8812,8813,8832,9269,9279,11279,11282,11310,11311,11475,11529,11531,11815,11942,12001,12374,12428,12429,12430,12431,12440,12441,12442,12443,12444,12445,12446,12447,13140,13338,13339,13340,13342,13343,13489,13490,13491,13492,13494,13499,13502,13503,13505,13700,13702,13703,13705,13706,13707,13708,13709,13720,13721,13726,13727,13728,13729,13732,13733,13736,13746,13917,13920,13924,13925,13929,13930,13932,13937,13979,13995,14142,14266,14268,14278,14282,14284,14461,14469,14472,14473,14474,14490,14507,14511,14795,14859,14865,14868,14927,14936,14944,14945,14947,14949,14950,14954,14977,15044,15047,15048,15065,15069,15070,15072,15074,15165,15166,15167,15180,15182,15230,15416,15444,15446,15447,15451,15455,15457,15459,15461,15531,15533,15551,15686,15689,15727,15728,15729,15730,15731,15732,15733,15809,16000,16001,16008,16009,16010,16153,16155,16158,16160,16161,16180,16181,16207,16214,16215,16216,16219,16518,16524,16526,17065,17075,17116,17117,17567,17568,17570,17611,17614,17621,17625,17626,17713,17714,17715,17864,17867,17869,17970,17974,17975,17977,17978,17983,17984,17987,18198,18357,18358,18359,18364,18367,18385,18441,18537,18540,18563,18729,18731,18733,18735,18738,18739,18740,18741,18743,19068,19073,19074,19233,19234,19235,19236,19401,19402,19404,19405,19406,19968,20126,20127,20128,20129,20130,20131,20292,20649,20650,20651,20652,20653,20654,20655,20656,20658,20659,20660,20661,20815,20817,20818,20905,20906,20907,20910,20912,20914,20939,20941,20942,20961,20963,20964,20965,20966,20967,20972,21012,21013,21205,21208,21210,435,1612,6488,7262,7263,7264,7265,7290,7291,8496,9383,11795,11892,14141,16150,16210,438,1893,2464,2496,2501,2512,2573,2591,2594,2644,2667,8063,8381,8798,11135,11219,11230,11238,11312,11524,11882,11921,11929,11943,11945,13493,13972,13991,14458,15040,15175,16515,17615,17973,18369,18370,18730,19411,20820,1461,1462,7652,12734,12743,16419,16420,17060,286,519,1334,1730,1978,1984,1986,6601,8761,10909,12432,12433,12434,12435,12436,12437,12438,12439,13154,13718,15691,17031,18366,1333,1335,1337,1338,1340,1341,1342,1343,1345,1346,1347,1348,2529,2538,2541,1751,1755,1758,1760,1761,1763,1780,1781,1783,1785,1787,1789,1791,1793,1795,1797,1798,1800,1802,1804,1806,1808,1845,1848,1851,1852,1854,1976,11508,11509,598,600,602,603,607,1750,1754,1757,1759,1762,1779,1782,1784,1786,1788,1790,1792,1794,1796,1799,1801,1803,1805,1807,1844,1846,1847,1849,1853,2187,11106,11500,16461,16462,16463,16464,16465,16466,16467,16468,16469,16470,16471,16472,17994,291,371,388,409,418,646,649,650,761,840,1672,1690,1890,1894,2248,2282,2301,2315,2559,2574,2621,6441,7258,7275,7276,7277,7278,7284,7292,7293,7294,7295,7297,7343,8059,8060,8061,8062,8065,8066,8085,8250,8251,8311,8321,8322,8389,8393,8397,8399,8403,8405,8481,8484,8570,8571,8754,8755,8756,8763,8764,8786,8795,11227,11229,11241,11250,11321,11523,11814,11924,11936,12076,12741,13335,13608,13697,13698,13704,13923,13934,13935,13940,14459,14468,14470,14862,14864,15039,15041,15043,15051,15169,15178,15228,15725,15726,15810,16148,17114,17616,17620,17641,17976,17979,18362,18542,18734,19072,19972,20908,20943,21271,301,414,415,416,1141,1670,1685,1889,1892,2247,2602,2605,2675,6897,6898,6899,6900,6902,6903,8158,8249,8252,8309,8387,8557,8766,11223,11232,11257,12016,13165,13167,13717,13719,13722,13941,13984,13989,14512,15071,15075,15720,16512,17622,19237,20960,374,380,395,421,428,1324,1332,1336,1344,1428,2254,2318,2365,2624,2661,2665,7267,7268,7287,7288,7903,8749,8750,8751,8752,8753,8777,8778,8779,8780,8781,8782,9388,9389,13529,13945,13946,14475,16378,16379,16380,16381,16385,16386,16389,16390,17992,459,6868,7653,7654,7655,7656,7657,7658,7659,7660,7661,7662,7663,7664,7665,7666,7667,7668,7669,7670,7671,7672,8078,11193,16489,16490,16491,16492,16493,16494,16495,16496,16497,16498,16499,315,316,463,464,465,466,467,918,929,965,1575,1660,1679,6490,7026,7027,7028,7222,7223,7224,7225,7369,7370,8762,8916,8998,9941,10835,11330,11517,11559,11560,11561,11562,12468,14279,14290,14635,15176,15177,15181,15263,15271,16156,16208,18539,18543,20969,20970,274,431,448,450,543,544,545,546,547,1326,2251,2304,2305,2306,7023,7024,7025,8102,8373,8374,8375,8376,8394,8402,8729,8731,8732,8733,8734,8735,8736,8737,9940,11323,11324,11325,11327,11329,11331,11332,11333,11518,11522,11530,11543,11544,11545,11546,11547,11548,11549,11550,11551,11552,11553,11554,11555,11556,11557,11558,12375,13731,14952,17021,17022,17553,17988,19971,20452,6603,6605,6606,6607,6608,6610,6611,6612,6613,6614,6615,6616,6617,6618,6619,6620,6621,6624,6658,7922,7923,362,363,364,1613,1663,1681,1891,2264,2507,2563,6739,6740,7030,7260,7273,7279,7280,7281,7282,7283,7298,7299,7300,7301,7302,7303,8050,8052,8053,8054,8084,8312,8314,8315,8352,8353,8354,8355,8356,8360,8362,8367,8383,8385,8388,8390,8391,8392,8401,8404,8482,8483,8491,8492,8493,8546,8680,8681,8682,8996,8997,9401,11225,11318,11527,12467,13593,13594,13595,13596,14263,14265,14996,14997,14998,15042,15172,15688,15723,16522,17115,17118,17612,17972,17981,18368,18565,20909,20944,20959,21207,21263,365,1652,1673,2060,2288,2451,2490,2502,2571,7285,11252,11335,11520,11895,13341,13724,13943,14457,14510,15067,15452,15685,15716,16002,16159,17609,17985,18564,20814,305,310,366,375,379,382,383,387,407,408,411,412,413,430,432,433,439,479,481,848,1142,1327,1614,1664,1675,1680,1709,2179,2245,2249,2252,2259,2279,2300,2322,2323,2336,2463,2465,2483,2509,2514,2516,2528,2554,2581,2598,2599,2642,2643,2664,2666,6738,7274,7403,7404,7405,7406,7407,7408,7410,7411,7906,8055,8056,8057,8058,8083,8313,8327,8328,8344,8345,8346,8347,8357,8358,8361,8363,8365,8368,8386,8408,8409,8410,8488,8489,8490,8515,8730,8738,8739,8740,8741,8744,8802,8803,9077,9078,9079,9080,9081,9082,9083,9084,9085,9086,9087,9088,11118,11122,11222,11228,11240,11242,11251,11254,11256,11309,11316,11322,11326,11334,11340,11525,11526,11880,11883,11922,11923,11927,11930,11931,11933,11939,11944,11946,11949,12015,12242,12243,12451,13096,13097,13157,13164,13168,13169,13274,13334,13495,13497,13498,13500,13504,13713,13714,13715,13716,13922,13926,13927,13928,13931,13933,13938,13939,13942,13949,13950,13971,13973,13974,13975,13983,13985,13986,13987,13993,13994,14264,14267,14280,14462,14463,14467,14477,14514,14515,14817,14822,14928,14951,15045,15046,15076,15168,15170,15171,15173,15179,15229,15450,15454,15456,15460,15717,15719,15721,15722,16051,16218,16525,17441,17569,17613,17619,17971,17980,17982,17990,18383,18538,18562,18566,18732,19069,19232,19399,19400,19407,19974,19975,20819,20945,21212,21256,21268,360,419,2317,2497,2551,2647,6844,11119,11231,11247,11248,11336,11519,11887,11920,11926,11928,11934,13155,13976,14476,14509,14516,15049,17618,18541,20940,367,844,845,846,847,2654,2673,11233,11328,11533,12448,13723,15066,15541,20971,2056,2283,2453,2460,2491,2495,2552,2578,2579,2580,2583,6841,9277,11121,11246,11255,11320,11521,11886,11896,11919,11935,11948,12449,13156,13913,13988,14145,14478,14853,14948,15073,15174,15684,15718,16149,16162,16163,16213,16220,17610,17986,18411,18568,19071,19231,19403,20816,21206,21265,12832,12833,12834,12835,12836,12837,12845,12860,12861,12862,12863,12876,12970,15008,20668,20669,20680,312,313,350,441,442,443,444,445,446,447,480,7032,7033,11110,17995,1526,1527,1528,1529,1531,1532,1533,1534,1535,1536,1537,1538,1634,17965,17967,1459,1460,1463,1468,12075,13193,18742,302,361,537,538,1974,1975,14479,384,880,881,882,885,1694,2998,7905,8406,11117,11136,11221,11338,11339,11341,11937,13982,16217,16511,16358,16507,16666,16667,16668,16841,16842,16844,16845,16846,16847,16848,16849,16850,16851,16852,16853,16854,16855,16856,16857,16858,16859,16860,16861,16862,16863,16864,16865,16866,16867,16869,16870,16871,16872,16873,16874,16876,16877,16878,16880,16881,16882,16883,16884,16885,16886,16887,16951,16955,16956,16957,16958,16959,16960,16961,16962,16963,16964,16965,16966,16967,16968,16969,16970,16971,16972,16973,16974,16975,16976,16981,16982,16983,16984,16985,16986,16987,16988,16989,16990,16991,16992,16993,16994,16995,16996,16997,16998,389,390,396,397,399,1973,8520,11226,11313,11497,14823,18209,287,288,300,391,392,393,394,422,427,2150,6671,7059,8785,11199,11239,13336,13730,15793,16384,368,369,370,757,2241,2242,2436,2437,8310,8712,12737,12738,12740,13060,13061,13062,13365,13366,13367,381,398,997,1325,1678,1734,1736,2324,2325,2326,2329,2331,2674,10831,20231,15379,15380,15381,15383,15384,15385,15386,15387,15388,15389,15390,15394,15395,15396,15397,15398,15480,15481,15482,15483,15484,15535,15536,15537,15538,15539,15902,15903,16022,16023,16024,16025,16026,16027,16028,16029,16030,16031,16032,16034,16035,16036,16037,16038,17222,17223,17224,17225,17226,17227,17228,17229,17232,17233,17234,17235,17236,17237,17238,17239,17242,17243,17244,17245,17246,17249,17250,17251,17252,17253,17257,17258,17259,17260,17261,17644,17645,17646,17647,17648,17649,17651,17652,306,309,593,594,1339,1772,1774,1776,1778,2253,2557,2659,6842,7020,7266,7904,8157,8745,8746,8747,8748,9381,9382,9384,9385,9386,9387,9391,11102,11501,11695,13166,16382,16383,16387,16388,16513,18361,285,529,531,535,690,2019,2022,2027,2030,2033,2037,7068,11502,424,426,651,652,653,654,1696,2273,2277,2278,2281,2285,2503,2513,2550,7259,7304,7305,7306,7307,7308,7379,7380,7381,7382,7383,7384,7385,7393,8002,8082,8380,8384,8398,8400,8485,8486,8487,8791,8800,8801,8808,8994,8995,11249,11253,11319,11516,11925,11932,11940,13487,13916,14921,15050,15219,15414,18318,21264,14645,14646,14647,14648,14649,14650,14651,14652,14653,14654,14655,14656,14657,14658,14659,14660,14661,14662,14663,14664,14665,14666,14667,14668,14669,14670,14671,14672,14673,14674,14675,14676,14677,14678,14679,14680,14681,14682,14683,14684,14685,14686,14687,14688,14689,14690,14691,14692,14693,14694,14695,14696,14697,14698,14699,14700,14701,14702,14703,14704,14705,14706,14720,14774,14975,15095,15272,15273,15274,15362,15702,15703,15704,15705,15734,15735,15736,16285,16286,17037,17414,17735,17736,17737,19905,19906,19907,19908,19909,19910,19911,19912,19913,19914,19915,19916,19921,19938,21217,434,1610,1682,6486,7269,7270,7271,7272,8494,8770,8771,8772,8773,8774,8775,9263,11891,16151,16154,16211,304,486,487,883,884,886,887,901,2271,2275,2287,2511,2555,2999,7907,8323,8324,8407,11120,11243,11337,11342,11884,11938,13196,13915,13918,13919,13921,13980,14860,14946,308,842,843,1129,8348,8349,8350,8351,8369,8370,8371,8372,8769,8776,8792,8793,11314,11317,12450,15687,19409,19412,961,962,963,1568,1569,1570,6884,7309,7310,7311,7312,7509,7510,7511,7557,7908,7918,7924,7926,7927,8073,8135,8139,8142,8143,8156,8307,8320,8329,8342,8343,8436,8437,8438,8439,8476,8477,8545,8917,8971,8972,8975,8977,9247,9248,9249,9250,9251,9252,9254,11174,11175,11176,11177,11178,11179,11180,11181,11636,11798,11799,12017,12073,12150,12151,12152,12351,12735,13333,14043,14044,14045,14046,14047,14464,14465,14560,14870,14935,15093,15162,15278,15279,15280,15475,15476,15477,15690,15806,15807,15808,15991,16179,17112,17113,17563,17564,17565,18066,18067,18068,18421,18544,18552,18736,19041,19049,19216,19514,19515,19963,20918,20919,20920,21216,21249,290,377,378,1674,9267,9278,9280,9281,11885,11888,11889,11890,11893,11894,13977,13978,14144,15485,16165,16166,16167,16168,16523,289,400,401,405,423,425,532,533,1671,2018,2021,2026,2029,2032,2035,2036,7018,11109,11499,16452,16453,16454,16455,16456,16457,17993,2290,2291,2292,2293,2303,2487,2609,2610,2611,2613,8138,8159,8308,13944,14819,15415,18371,18567,437,1611,6487,6736,8495,13528,303,449,460,461,470,471,472,473,474,476,920,926,1002,1329,2250,2357,2358,2379,2564,2565,6489,7013,7014,7015,7016,7017,7261,7286,7289,7925,8796,8797,11107,11315,11528,11532,14460,14513,16487,16488,17991,746,747,748,749,750,6457,6458,7035,7036,11280,12728,13981,16440,307,376,648,1022,1455,1456,1457,1458,2586,2588,8141,8161,8359,8767,11224,11941,13488,13725,13990,13992,15068,15453,15715,17989,19070]
        self.itemsleft = [None] * len(self.items)
        self.timer = time.time()

    def send_to_server(self, data):
        if isinstance(data, Msg):
            data.count = self.counter + 1
            print("Injected : ",end="")
            print(data.json())
            data = data.bytes()
        self.injected_to_server += 1
        self.coSer.sendall(data)



    def send_message(self, s):
        msg = Msg.from_json(
            {"__type__": "ChatClientMultiMessage", "content": s, "channel": 0}
        )
        self.send_to_server(msg)






    def ask_item_price(self, itemid):
        msg= Msg.from_json(
            {'__type__': 'ExchangeBidHouseSearchMessage', 'genId': itemid, 'follow': True}
        )
        self.send_to_server(msg)

    def disconnect_hdv(self):
        msg= Msg.from_json(
            {'__type__': 'LeaveDialogRequestMessage'}
        )
        self.send_to_server(msg)

    def connect_hdv(self):
        msg= Msg.from_json(
            {'__type__': 'InteractiveUseRequestMessage', 'elemId': 522694, 'skillInstanceUid': 136144973}
        )
        self.send_to_server(msg)




    def handle(self, data, origin):

        super().handle(data, origin)
        #self.other[origin].sendall(data)
        self.buf[origin] += data
        from_client = origin == self.coJeu
        # print(direction(origin), self.buf[origin].data)
        msg = Msg.fromRaw(self.buf[origin], from_client)
        while msg is not None:
            if msg.id in protocol.msg_from_id:
                msgType = protocol.msg_from_id[msg.id]
                parsedMsg = protocol.read(msgType, msg.data)
                assert msg.data.remaining() == 0, (
                    "All content of %s have not been read into %s:\n %s"
                    % (msgType, parsedMsg, msg.data)
                )
                self.handle_message(parsedMsg, origin)
            else:
                print('sorry, no '+str(msg.id))
            msg = Msg.fromRaw(self.buf[origin], from_client)


    def handle_message(self, msg, origin):
#        print(direction(origin))
        if self.script == "on":
            if len(self.itemsleft) == 0:
                self.itemsleft = [None] * len(self.items)
                for i in range(0, len(self.items)):
                    self.itemsleft[i] = self.items[i]
#                self.script = "off"
            else:
                datenow = time.time()
                diff = datenow - self.timer
                if diff > 0.8:
                    if (self.injections%3) == 2:
                        self.disconnect_hdv()
                        self.connect_hdv()
                    else:
                        self.ask_item_price(self.itemsleft.pop())
                        self.timer = datenow
                    self.injections = self.injections + 1
# mask game internals functions
        if msg["__type__"] not in ["GameMapMovementMessage","SetCharacterRestrictionsMessage","GameContextRefreshEntityLookMessage","GameRolePlayShowActorMessage","GameMapChangeOrientationMessage","UpdateMapPlayersAgressableStatusMessage","GameContextRemoveElementMessage","ChatServerMessage","ChatServerWithObjectMessage"] or direction(origin) == "Client->Server":
#            if msg["__type__"] == "InteractiveUseRequestMessage":
            print(direction(origin))
            print(msg)

# retreive price and export it
            if msg["__type__"] == "ExchangeTypesItemsExchangerDescriptionForUserMessage":
               if len(msg["itemTypeDescriptions"]) > 0:
                   print("--------------- GOT ONE ---------------")
                   itemID = msg["itemTypeDescriptions"][0]["objectGID"]
                   pricesArray = msg["itemTypeDescriptions"][0]["prices"]
                   print(itemID)
                   print(pricesArray)
                   prices = str(pricesArray[0]) + ',' + str(pricesArray[1]) + ',' + str(pricesArray[2])
                   requests.get('https://o-sens-propre.fr/dodo/add.php?token=456789tyhujizoefiuho678945&itemID='+str(itemID)+'&prices='+prices)
                   print("--------------- GOT ONE ---------------")

# execute our injection : start it by sending any message on chat to /general
            elif msg["__type__"] == "ChatClientMultiMessage":
                print(msg["content"])
                if msg["content"] == "stop":
                    self.script = "off"
                    self.send_message("stooooooop")
                elif msg["content"] == "lessgo":
                    self.script = "on"
                    self.timer = time.time()
                    self.itemsleft = [None] * len(self.items)
                    for i in range(0, len(self.items)):
                        self.itemsleft[i] = self.items[i]
                    self.send_message("gooooooooo")
#                self.send_message("azerteyuioop")
#                    for i in [0,1,2,3,4,5,7,8,9]: DO THIIIIIIIIIS in any packet when script is on
#                        self.ask_item_price(307+i)
#                        time.sleep(4)
#            self.ask_item_price(11971)
#            print(msg.json()) # for debug with crash
