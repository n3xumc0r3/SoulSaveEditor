package com.n3xumc0r3.soulsaveeditor.gamefiles;

public class KeyProvider {
	public static byte[] getStatisticKey() {
		return new byte[]{0x63, 0x72, 0x73, 0x74, 0x31, 0x0, 0x0, 0x0};
	}

	public static byte[] getDefaultKey() {
		return new byte[]{0x69, 0x61, 0x6d, 0x62, 0x6f, 0x0, 0x0, 0x0};
	}

	public static byte[] getIv() {
		return new byte[]{
				0x41,
				0x68,
				0x62,
				0x6f,
				0x6f,
				0x6c,
				0x0,
				0x0
		};
	}

	public static byte[] getXorKey() {
		return new byte[]{
				115,
				108,
				99,
				122,
				125,
				103,
				117,
				99,
				127,
				87,
				109,
				108,
				107,
				74,
				95
		};
	}
}
