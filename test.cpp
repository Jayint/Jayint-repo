#include<bits/stdc++.h>
using namespace std;
int a[10][10];

int main()
{
	int n;
    cin >> n;
    int cur = 1, x = 1, y = 1;
    a[x][y] = 1;
    while(cur <= n * n){
        //向右
        while(y + 1 <= n && a[x][y + 1] == 0){
            y++;
            a[x][y] = ++cur;
        }
        //向下
        while(x + 1 <= n && a[x + 1][y] == 0){
            x ++;
            a[x][y] = ++cur;
        }
        //向左
        while(y - 1 >= 1 && a[x][y - 1] == 0){
            y--;
            a[x][y] = ++cur;
        }
        //向上
        while(x - 1 >= 1 && a[x - 1][y] == 0){
            x --;
            a[x][y] = ++cur;
        }
        if(cur == n * n) break;
    }
    for(int i = 1; i <= n; i++){
        for(int j = 1; j <= n; j++){
            printf("%3d ", a[i][j]);
        }
        cout << '\n';
    }
}